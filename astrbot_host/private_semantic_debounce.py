"""Optional local sentence-completion scoring for private text intake."""

import asyncio
import logging
from pathlib import Path


logger = logging.getLogger(__name__)


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
    options = config.get("private_debounce_semantic", {})
    if not isinstance(options, dict) or not options.get("model_dir"):
        return None
    try:
        return LocalCompleteness(options)
    except (KeyError, ValueError, TypeError):
        logger.warning("私聊语义防抖配置无效，使用计时规则")
        return None
