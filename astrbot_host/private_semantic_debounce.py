"""Optional local ONNX and Laya turn-completeness scoring for private text intake."""

import asyncio
import importlib.util
import logging
from pathlib import Path


logger = logging.getLogger(__name__)

FAST_PATH_LONG_TEXT_CHARS = 40
FAST_PATH_MIN_REMAINING_SECONDS = 0.3
DEFAULT_LAYA_MAX_TIMEOUT_SECONDS = 2.0
_INCOMPLETE_SUFFIXES = (
    "...", "……", "…", "，", ",", "、", "；", ";", "：", ":", "—", "--", "~", "～",
)
_TERMINAL_PUNCTUATION = ("。", "！", "？", "!", "?")


def fast_path_complete_reason(text, remaining) -> str | None:
    """Return a non-empty skip reason when a burst can proceed without calling Laya.

    Any truthy return value means the burst is treated as complete in 0ms with
    zero network/model overhead.
    """
    if remaining is not None:
        try:
            if float(remaining) <= FAST_PATH_MIN_REMAINING_SECONDS:
                return "budget_too_short"
        except (TypeError, ValueError):
            return "budget_too_short"
    stripped = str(text or "").strip()
    if not stripped:
        return "empty"
    if stripped.endswith(_INCOMPLETE_SUFFIXES):
        return None
    if stripped.endswith(_TERMINAL_PUNCTUATION):
        return "terminal_punctuation"
    visible_len = sum(1 for char in stripped if not char.isspace())
    if visible_len >= FAST_PATH_LONG_TEXT_CHARS:
        return "long_complete"
    return None


class LocalCompleteness:
    def __init__(self, options):
        self.threshold = float(options.get("threshold", 0.8))
        self.wait_seconds = options.get("maximum_wait_ms", 15000) / 1000
        self.timeout = options.get("inference_timeout_ms", 1500) / 1000
        self.directory = Path(options["model_dir"]).expanduser()
        if not (0 < self.threshold <= 1 and 0.01 <= self.wait_seconds <= 120
                and 0.01 <= self.timeout <= 10 and self.directory.is_absolute()):
            raise ValueError("invalid semantic debounce settings")
        self.closed = False
        self.worker = None
        self.model = asyncio.create_task(asyncio.to_thread(self.load), name="companion:semantic-debounce-model")
        self.model.add_done_callback(self.loaded)

    @staticmethod
    def loaded(task):
        if task.cancelled():
            return
        error = task.exception()
        if error:
            logger.warning("私聊语义防抖模型加载失败，使用计时规则 error=%s", type(error).__name__)
        else:
            logger.info("私聊语义防抖本地模型就绪 backend=onnx")

    def load(self):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(str(self.directory / "tokenizer/tokenizer.json"))
        tokenizer.enable_truncation(max_length=64, direction="left")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(self.directory / "model.onnx"),
                                       sess_options=options, providers=["CPUExecutionProvider"])
        return tokenizer, session

    def predict(self, text):
        import numpy as np

        tokenizer, session = self.model.result()
        encoded = tokenizer.encode(text[-2048:])
        values = {"input_ids": encoded.ids, "attention_mask": encoded.attention_mask,
                  "token_type_ids": encoded.type_ids}
        logits = session.run(["logits"], {
            field.name: np.asarray([values[field.name]], dtype=np.int64) for field in session.get_inputs()
        })[0][0]
        probabilities = np.exp(logits - logits.max())
        return float(probabilities[1] / probabilities.sum())

    async def score(self, text, remaining):
        if (self.closed or remaining <= 0 or not self.model.done()
                or self.model.cancelled() or self.model.exception()):
            return None
        if self.worker is not None and not self.worker.done():
            return None
        self.worker = asyncio.create_task(asyncio.to_thread(self.predict, text), name="companion:semantic-debounce")
        self.worker.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        try:
            score = await asyncio.wait_for(asyncio.shield(self.worker), min(self.timeout, remaining))
            return score if 0 <= score <= 1 else None
        except asyncio.CancelledError:
            if not self.closed:
                raise
            return None
        except Exception as error:
            logger.warning("私聊语义判断失败，使用计时规则 error=%s", type(error).__name__)
            return None

    def close(self):
        self.closed = True
        for task in (self.model, self.worker):
            if task is not None and not task.done():
                task.cancel()


def classifier_from_config(config):
    options = config.get("private_debounce_semantic", {}) if isinstance(config, dict) else {}
    if not isinstance(options, dict) or not options.get("model_dir"):
        return None
    try:
        return LocalCompleteness(options)
    except (KeyError, ValueError, TypeError):
        logger.warning("私聊语义防抖配置无效，使用计时规则")
        return None


class LayaTurnCompleteness:
    """Single-head turn_state completeness classifier backed by the configured Laya service."""

    def __init__(self, service_resolver, *, options=None, wait_seconds=None,
                 max_timeout=DEFAULT_LAYA_MAX_TIMEOUT_SECONDS):
        self.service_resolver = service_resolver
        raw_options = options if isinstance(options, dict) else {}
        if wait_seconds is None:
            raw_wait = raw_options.get("maximum_wait_ms", 15000)
            try:
                resolved_wait = float(raw_wait) / 1000.0
            except (TypeError, ValueError):
                resolved_wait = 15.0
            self.wait_seconds = min(120.0, max(0.01, resolved_wait))
        else:
            self.wait_seconds = min(120.0, max(0.01, float(wait_seconds)))
        self.max_timeout = max(0.1, float(max_timeout))
        self.closed = False

    async def check_incomplete(self, text, remaining, *, context=None,
                               input_parts=(), event_ref=None) -> bool | None:
        """Return True if Laya says the burst is unfinished, False if complete, None on fail-open."""
        if self.closed:
            return None
        skip_reason = fast_path_complete_reason(text, remaining)
        if skip_reason is not None:
            logger.debug("[CompanionV2] 私聊 Laya 补话判断直通放行 reason=%s", skip_reason)
            return False
        service = self.service_resolver(context) if callable(self.service_resolver) else None
        if service is None:
            return None
        budget = min(self.max_timeout, max(0.0, float(remaining)))
        store = getattr(service, "store", None)
        if store is not None and callable(getattr(store, "raw", None)):
            try:
                values = store.raw()
                if not values.get("enabled"):
                    return None
                configured_timeout = values.get("timeout_seconds")
                if type(configured_timeout) in (int, float) and configured_timeout > 0:
                    budget = min(budget, float(configured_timeout))
            except Exception:
                return None
        if budget <= 0.05:
            return None
        set_trace = getattr(service, "set_trace", None)
        if callable(set_trace):
            set_trace(event_ref=event_ref, source="private_debounce")
        facts = {
            "scope_kind": "private",
            "user_text": str(text or "")[-2000:],
            "input_parts": [str(item) for item in input_parts[-8:] if str(item).strip()],
        }
        try:
            result = await asyncio.wait_for(
                service.classify_turn_completeness(facts, timeout_seconds=budget),
                timeout=budget + 0.15,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("[CompanionV2] 私聊 Laya 补话判断失败，按计时规则收口 code=%s",
                           getattr(error, "code", type(error).__name__))
            return None
        if not isinstance(result, dict) or result.get("status") != "ok":
            return None
        return result.get("turn_state") == "unfinished"

    def close(self):
        self.closed = True


def _onnx_readiness(config, *, onnx_classifier=None) -> dict:
    options = config.get("private_debounce_semantic", {}) if isinstance(config, dict) else {}
    if not isinstance(options, dict) or not str(options.get("model_dir") or "").strip():
        return {"ready": False, "reason": "model_dir_missing"}
    raw_dir = str(options.get("model_dir") or "").strip()
    try:
        directory = Path(raw_dir).expanduser()
        threshold = float(options.get("threshold", 0.8))
        wait_seconds = float(options.get("maximum_wait_ms", 15000)) / 1000.0
        timeout = float(options.get("inference_timeout_ms", 1500)) / 1000.0
        if not (0 < threshold <= 1 and 0.01 <= wait_seconds <= 120
                and 0.01 <= timeout <= 10 and directory.is_absolute()):
            return {"ready": False, "reason": "config_invalid"}
    except (TypeError, ValueError, OSError):
        return {"ready": False, "reason": "config_invalid"}
    if onnx_classifier is not None:
        model_task = getattr(onnx_classifier, "model", None)
        if model_task is not None and model_task.done() and not model_task.cancelled():
            error = model_task.exception()
            if error is None:
                return {"ready": True, "reason": "ready"}
            if isinstance(error, ModuleNotFoundError):
                return {"ready": False, "reason": "dependencies_missing"}
            if isinstance(error, (FileNotFoundError, OSError)):
                return {"ready": False, "reason": "model_files_missing"}
            return {"ready": False, "reason": "load_failed"}
    has_deps = (importlib.util.find_spec("onnxruntime") is not None
                and importlib.util.find_spec("tokenizers") is not None)
    if not has_deps:
        return {"ready": False, "reason": "dependencies_missing"}
    if not ((directory / "model.onnx").is_file() and (directory / "tokenizer" / "tokenizer.json").is_file()):
        return {"ready": False, "reason": "model_files_missing"}
    if onnx_classifier is not None:
        model_task = getattr(onnx_classifier, "model", None)
        if model_task is not None and not model_task.done():
            return {"ready": False, "reason": "loading"}
    return {"ready": True, "reason": "ready"}


def _laya_readiness(data_root) -> dict:
    if data_root is None:
        return {"ready": False, "mode": "", "model": "", "reason": "config_unreadable"}
    try:
        from companion_v2.decision.store import LayaSettingsStore
        info = LayaSettingsStore(Path(data_root)).read()
    except Exception:
        return {"ready": False, "mode": "", "model": "", "reason": "config_unreadable"}
    mode = str(info.get("mode") or "")
    model = str(info.get("default_model") or "")
    if not info.get("enabled"):
        return {"ready": False, "mode": mode, "model": model, "reason": "disabled"}
    if mode == "local":
        if not info.get("package_installed"):
            return {"ready": False, "mode": mode, "model": model, "reason": "package_missing"}
        try:
            from .laya_client import local_runtime_status
            runtime = local_runtime_status(local_model_path=info.get("local_model_path", ""))
            if runtime.get("local_runtime_state") == "gpu_unavailable":
                return {"ready": False, "mode": mode, "model": model, "reason": "gpu_unavailable"}
        except Exception:
            pass
        if not info.get("model_cache_ready"):
            return {"ready": False, "mode": mode, "model": model, "reason": "model_missing"}
        return {"ready": True, "mode": mode, "model": model, "reason": "ready"}
    if not str(info.get("endpoint") or "").strip():
        return {"ready": False, "mode": mode, "model": model, "reason": "endpoint_missing"}
    return {"ready": True, "mode": mode, "model": model, "reason": "ready"}


def semantic_debounce_status(config, *, data_root=None, onnx_classifier=None) -> dict:
    """Return secret-free, URL-free readiness metadata for ONNX and Laya debounce backends."""
    return {
        "onnx": _onnx_readiness(config, onnx_classifier=onnx_classifier),
        "laya": _laya_readiness(data_root),
    }
