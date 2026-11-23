from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .common import Difficulty

SoupContentLevel = Literal["all_ages", "normal", "unrestricted"]
SoupVerdict = Literal["yes", "no", "irrelevant", "partial", "compound"]
SoupMode = Literal["bot_host", "player_host"]
SoupEntryKind = Literal["question", "answer", "hint", "reverse"]
SoupBotAction = Literal["question", "guess"]


VERDICT_LABELS: dict[SoupVerdict, str] = {
    "yes": "是",
    "no": "否",
    "irrelevant": "无关",
    "partial": "部分正确",
    "compound": "请一次只问一个问题",
}


@dataclass(slots=True, frozen=True)
class SoupPuzzle:
    title: str
    surface: str
    solution: str
    key_facts: tuple[str, ...]
    acceptable_variants: tuple[str, ...]
    hints: tuple[str, ...]
    content_level: SoupContentLevel
    theme: str
    trick: str

    @property
    def signature(self) -> str:
        return f"{self.theme.strip().lower()}|{self.trick.strip().lower()}"[:180]

    def public_snapshot(self, *, reveal_solution: bool) -> dict[str, object]:
        data: dict[str, object] = {
            "title": self.title,
            "surface": self.surface,
            "content_level": self.content_level,
        }
        if reveal_solution:
            data["solution"] = self.solution
            data["key_facts"] = list(self.key_facts)
        return data


@dataclass(slots=True)
class SoupEntry:
    kind: SoupEntryKind
    prompt: str
    response: str
    source: Literal["web", "qq", "system"]
    verdict: SoupVerdict | None = None
    player_number: int | None = None
    bot_action: SoupBotAction | None = None
    at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "prompt": self.prompt,
            "response": self.response,
            "source": self.source,
            "verdict": self.verdict,
            "player_number": self.player_number,
            "bot_action": self.bot_action,
            "at": self.at,
        }


@dataclass(slots=True)
class TurtleSoupGame:
    difficulty: Difficulty
    max_hints: int
    content_level: SoupContentLevel
    mode: SoupMode = "bot_host"
    puzzle: SoupPuzzle | None = None
    phase: Literal["preparing", "ready", "finished"] = "preparing"
    processing: bool = False
    processing_player_number: int | None = None
    entries: list[SoupEntry] = field(default_factory=list)
    discovered_facts: set[int] = field(default_factory=set)
    question_count: int = 0
    answer_attempts: int = 0
    hints_used: int = 0
    solved: bool = False
    gave_up: bool = False
    bot_solved: bool = False
    turn_count: int = 0
    last_bot_action: SoupBotAction | None = None
    last_bot_text: str = ""
    failure_reason: str = ""

    def __post_init__(self) -> None:
        if self.mode == "player_host" and self.phase == "preparing":
            self.phase = "ready"

    @property
    def finished(self) -> bool:
        return self.phase == "finished"

    @property
    def draw(self) -> bool:
        return False

    @property
    def winner(self) -> str:
        if not self.finished:
            return ""
        if self.mode == "player_host":
            return "bot"
        return "human" if self.solved else "bot"

    @property
    def history(self) -> list[SoupEntry]:
        return self.entries

    def set_puzzle(self, puzzle: SoupPuzzle) -> None:
        if self.mode != "bot_host":
            raise ValueError("玩家出题模式不使用 Bot 题库")
        if self.phase != "preparing":
            raise ValueError("当前海龟汤已经完成出题")
        self.puzzle = puzzle
        self.phase = "ready"
        self.failure_reason = ""

    def begin_processing(self) -> None:
        if self.phase == "preparing":
            raise ValueError("Bot 还在准备题目")
        if self.phase == "finished":
            raise ValueError("本题已经结束")
        if self.processing:
            raise ValueError("Bot 正在判断上一条内容")
        if self.mode == "bot_host" and self.puzzle is None:
            raise ValueError("题目尚未准备完成")
        self.processing = True

    def cancel_processing(self, reason: str = "") -> None:
        self.processing = False
        self.processing_player_number = None
        self.failure_reason = str(reason or "")[:160]

    def record_question(
        self,
        question: str,
        verdict: SoupVerdict,
        *,
        source: Literal["web", "qq"],
        matched_facts: set[int] | None = None,
        player_number: int | None = None,
    ) -> set[int]:
        if self.phase != "ready":
            raise ValueError("当前不能继续提问")
        newly_discovered = set(matched_facts or ()) - self.discovered_facts
        self.discovered_facts.update(matched_facts or ())
        self.question_count += 1
        self.entries.append(
            SoupEntry(
                kind="question",
                prompt=question,
                response=VERDICT_LABELS[verdict],
                source=source,
                verdict=verdict,
                player_number=player_number,
            )
        )
        del self.entries[:-80]
        self.processing = False
        self.processing_player_number = None
        self.failure_reason = ""
        return newly_discovered

    def record_answer(
        self,
        answer: str,
        *,
        solved: bool,
        source: Literal["web", "qq"],
        matched_facts: set[int] | None = None,
        player_number: int | None = None,
    ) -> set[int]:
        if self.phase != "ready":
            raise ValueError("当前不能提交答案")
        newly_discovered = set(matched_facts or ()) - self.discovered_facts
        self.discovered_facts.update(matched_facts or ())
        self.answer_attempts += 1
        response = (
            "推理正确，汤底已经揭晓。" if solved else "已经接近了，但还缺少关键环节。"
        )
        self.entries.append(
            SoupEntry(
                kind="answer",
                prompt=answer,
                response=response,
                source=source,
                player_number=player_number,
            )
        )
        del self.entries[:-80]
        self.processing = False
        self.processing_player_number = None
        self.failure_reason = ""
        if solved:
            self.solved = True
            self.phase = "finished"
        return newly_discovered

    def reveal_hint(
        self, *, source: Literal["web", "qq"], player_number: int | None = None
    ) -> str:
        if self.phase != "ready" or self.puzzle is None:
            raise ValueError("当前不能申请提示")
        allowed = len(self.puzzle.hints)
        if self.max_hints:
            allowed = min(allowed, self.max_hints)
        if self.hints_used >= allowed:
            raise ValueError("本题已经没有更多提示")
        hint = self.puzzle.hints[self.hints_used]
        self.hints_used += 1
        self.entries.append(
            SoupEntry(
                kind="hint",
                prompt="申请提示",
                response=hint,
                source=source,
                player_number=player_number,
            )
        )
        del self.entries[:-80]
        return hint

    def give_up(self, *, source: Literal["web", "qq"] = "qq") -> None:
        if self.phase != "ready" or (self.mode == "bot_host" and self.puzzle is None):
            raise ValueError("当前没有可以放弃的海龟汤")
        self.gave_up = True
        self.phase = "finished"
        self.processing = False
        self.processing_player_number = None
        self.entries.append(
            SoupEntry(
                kind="answer",
                prompt=(
                    "结束玩家出题" if self.mode == "player_host" else "放弃并查看汤底"
                ),
                response=(
                    "本题已结束。"
                    if self.mode == "player_host"
                    else "本题已结束，汤底已经揭晓。"
                ),
                source=source,
            )
        )
        del self.entries[:-80]

    def record_reverse_turn(
        self,
        player_text: str,
        *,
        bot_action: SoupBotAction,
        bot_text: str,
        source: Literal["web", "qq"],
        player_number: int | None = None,
    ) -> None:
        if self.mode != "player_host" or self.phase != "ready":
            raise ValueError("当前不是玩家出题回合")
        self.turn_count += 1
        if bot_action == "question":
            self.question_count += 1
        else:
            self.answer_attempts += 1
        self.last_bot_action = bot_action
        self.last_bot_text = bot_text
        self.entries.append(
            SoupEntry(
                kind="reverse",
                prompt=player_text,
                response=bot_text,
                source=source,
                player_number=player_number,
                bot_action=bot_action,
            )
        )
        del self.entries[:-80]
        self.processing = False
        self.processing_player_number = None
        self.failure_reason = ""

    def confirm_bot_guess(self, *, correct: bool) -> None:
        if self.mode != "player_host" or self.phase != "ready":
            raise ValueError("当前不能判定 Bot 的猜测")
        if self.last_bot_action != "guess":
            raise ValueError("Bot 目前还没有提交可判定的猜测")
        if correct:
            self.bot_solved = True
            self.solved = False
            self.phase = "finished"
        self.processing = False
        self.processing_player_number = None

    def snapshot(self) -> dict[str, object]:
        reveal_solution = self.finished and self.puzzle is not None
        available_hints = len(self.puzzle.hints) if self.puzzle else 0
        hint_limit = (
            min(self.max_hints, available_hints) if self.max_hints else available_hints
        )
        return {
            "kind": "turtle_soup",
            "mode": self.mode,
            "phase": self.phase,
            "preparing": self.phase == "preparing",
            "processing": self.processing,
            "failure_reason": self.failure_reason,
            "puzzle": self.puzzle.public_snapshot(reveal_solution=reveal_solution)
            if self.puzzle
            else None,
            "entries": [entry.snapshot() for entry in self.entries[-60:]],
            "question_count": self.question_count,
            "answer_attempts": self.answer_attempts,
            "hints_used": self.hints_used,
            "hint_limit": hint_limit,
            "discovered_fact_count": len(self.discovered_facts),
            "key_fact_count": len(self.puzzle.key_facts) if self.puzzle else 0,
            "solved": self.solved,
            "gave_up": self.gave_up,
            "bot_solved": self.bot_solved,
            "turn_count": self.turn_count,
            "last_bot_action": self.last_bot_action,
            "last_bot_text": self.last_bot_text,
            "finished": self.finished,
        }


def normalize_content_level(value: Any) -> SoupContentLevel:
    normalized = str(value or "normal").strip().lower()
    aliases = {
        "all_ages": "all_ages",
        "all-ages": "all_ages",
        "全年龄": "all_ages",
        "normal": "normal",
        "普通": "normal",
        "unrestricted": "unrestricted",
        "不限制": "unrestricted",
    }
    return aliases.get(normalized, "normal")  # type: ignore[return-value]


def puzzle_from_mapping(
    data: dict[str, Any], *, content_level: SoupContentLevel
) -> SoupPuzzle:
    title = _text(data.get("title"), 80)
    surface = _text(data.get("surface"), 800)
    solution = _text(data.get("solution"), 1600)
    key_facts = _text_items(data.get("key_facts"), maximum=8, item_limit=260)
    variants = _text_items(data.get("acceptable_variants"), maximum=8, item_limit=260)
    hints = _text_items(data.get("hints"), maximum=8, item_limit=260)
    theme = _text(data.get("theme"), 80)
    trick = _text(data.get("trick"), 160)
    if len(title) < 2:
        raise ValueError("题目标题过短")
    if len(surface) < 20:
        raise ValueError("汤面信息不足")
    if len(solution) < 30:
        raise ValueError("汤底信息不足")
    if len(key_facts) < 3:
        raise ValueError("关键事实不足")
    if len(hints) < 3:
        raise ValueError("渐进提示不足")
    if not theme or not trick:
        raise ValueError("题目缺少主题或核心诡计标记")
    return SoupPuzzle(
        title=title,
        surface=surface,
        solution=solution,
        key_facts=tuple(key_facts),
        acceptable_variants=tuple(variants),
        hints=tuple(hints),
        content_level=content_level,
        theme=theme,
        trick=trick,
    )


def fallback_puzzle(
    *,
    content_level: SoupContentLevel,
    excluded_signatures: set[str] | None = None,
) -> SoupPuzzle:
    excluded = excluded_signatures or set()
    candidates = [
        puzzle_from_mapping(
            item,
            content_level=normalize_content_level(item.get("content_level")),
        )
        for item in _FALLBACK_PUZZLES
        if item["content_level"] == "all_ages" or content_level != "all_ages"
    ]
    available = [item for item in candidates if item.signature not in excluded]
    return secrets.choice(available or candidates)


def clean_player_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError("内容不能为空")
    if len(text) > limit:
        raise ValueError(f"内容不能超过 {limit} 个字符")
    return text


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _text_items(value: Any, *, maximum: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= maximum:
            break
    return result


_FALLBACK_PUZZLES: tuple[dict[str, Any], ...] = (
    {
        "content_level": "all_ages",
        "title": "没有寄出的明信片",
        "surface": "小林旅行回来后，把一张写好的明信片放进抽屉。他没有寄出它，却因此确认朋友已经收到了自己的祝福。为什么？",
        "solution": "小林和朋友约定，各自在旅行地写一张明信片，但不邮寄，而是在回家后视频展示。朋友在视频中读出了小林明信片背面的内容，所以小林确认祝福已经通过视频传达；明信片本身仍留在抽屉里。",
        "key_facts": [
            "祝福并非依靠邮寄传达",
            "两人事先约定通过视频展示",
            "朋友已经在视频中读到明信片内容",
        ],
        "acceptable_variants": ["通过照片、直播或视频通话看到了明信片内容也可视为等价"],
        "hints": [
            "重点不是邮局出了问题。",
            "朋友看到了信息，但没有拿到那张纸。",
            "两人使用了实时影像交流。",
            "他们提前约定各自留存实体明信片作为纪念。",
            "朋友在视频通话里念出了明信片背面的祝福。",
        ],
        "theme": "旅行与通讯",
        "trick": "收到祝福不等于收到实体明信片",
    },
    {
        "content_level": "all_ages",
        "title": "最后一名的奖牌",
        "surface": "阿远在比赛中最后一个到达终点，却获得了唯一一枚奖牌，而且所有参赛者都认为公平。为什么？",
        "solution": "这是一场接力式的公益徒步，奖牌交给最后一棒的代表，由他代全队领取。阿远是整支队伍最后通过终点的人，也是被事先指定的领奖代表；比赛并不按个人到达顺序排名。",
        "key_facts": [
            "比赛不是个人竞速排名",
            "阿远代表一个团队",
            "唯一奖牌属于全队并由最后一棒代领",
        ],
        "acceptable_variants": ["团体完赛纪念活动、非竞速接力等设定可视为等价"],
        "hints": [
            "最后到达不一定代表成绩最差。",
            "奖牌并不只属于阿远个人。",
            "这是团体完成的接力活动。",
            "所有人都属于同一支需要全员抵达才算完赛的队伍。",
            "奖牌在全员通过终点时交给压阵的最后一棒代表。",
        ],
        "theme": "体育与团队",
        "trick": "把团体完赛误认为个人竞速",
    },
    {
        "content_level": "all_ages",
        "title": "不肯亮起的路灯",
        "surface": "小镇广场修好了一盏全新路灯，但每到晴朗的夜晚人们都故意关掉它；一旦阴天起雾，大家反而立刻打开。为什么？",
        "solution": "小镇是著名的观星点，广场上每逢晴夜都会举办天文观测活动，开灯会造成光污染影响观星；而阴天起雾时无法观星，为了行人安全便把路灯打开照明。",
        "key_facts": [
            "晴朗夜晚广场有怕强光的活动",
            "路灯的光线会干扰观星或天文观测",
            "阴天起雾无法观星，开灯是为了行人照明安全",
        ],
        "acceptable_variants": ["天文摄影、萤火虫观赏等需要晴夜暗环境的合理活动可视为等价"],
        "hints": [
            "路灯本身没有任何电路故障。",
            "晴天夜晚大家抬头看的东西害怕灯光。",
            "这与天空的能见度有关。",
            "灯光会产生光污染，影响夜晚的观测活动。",
            "小镇晴夜习惯在广场观星，阴天起雾不能观星才开灯照明。",
        ],
        "theme": "天文与小镇日常",
        "trick": "晴夜关灯是为了避免光污染观星",
    },
    {
        "content_level": "all_ages",
        "title": "倒着贴的门牌",
        "surface": "搬家后，阿宁特意把客厅墙上的挂钟数字和日历都镜像倒贴，朋友来访却夸他太聪明了。为什么？",
        "solution": "阿宁在客厅正对梳妆台或工作台的大镜子对面挂了时钟和日历。平时他总是背对挂钟面对镜子化妆或直播，把数字镜像倒贴后，从镜子里直接看过去反而是正向可读的。",
        "key_facts": [
            "阿宁平时并不是直接回头看挂钟和日历",
            "房间里有一面与墙面相对的镜子或前置镜像画面",
            "镜像倒贴让镜中的倒影变成正向可读的时间和日期",
        ],
        "acceptable_variants": ["前置摄像头镜像直播画面中展示正向时间也可视为等价"],
        "hints": [
            "阿宁并没有认字或视力障碍。",
            "他平时看挂钟的方式不是直接转头去看。",
            "这利用了某种光学反射现象。",
            "挂钟和日历正对着房间里的一面镜子。",
            "镜像倒贴后，从镜子里看到的数字正好是正着的。",
        ],
        "theme": "生活巧思与光学",
        "trick": "利用镜面反射让倒贴文字正向显示",
    },
    {
        "content_level": "all_ages",
        "title": "空着一半的书架",
        "surface": "社区图书馆新买了一批儿童绘本，管理员却故意把最上层全部空着，只把书密密麻麻摆在最下面两层。馆长看后立刻表扬了她。为什么？",
        "solution": "这批绘本的读者是身高有限的小朋友。管理员把绘本全部放在最下面两层，让孩子们不用踮脚或攀爬就能自己拿到书，同时避免上层掉落砸伤孩子。",
        "key_facts": [
            "这批书的读者主要是儿童",
            "放在最下面两层是为了方便孩子自行取阅",
            "空出上层避免了攀爬和坠落的安全隐患",
        ],
        "acceptable_variants": ["方便坐轮椅或低龄读者拿取的合理说明可视为等价"],
        "hints": [
            "不是因为书的数量不够摆满。",
            "这和借阅这批书的读者身体特征有关。",
            "如果放在高处，读者会遇到麻烦甚至危险。",
            "绘本主要是给个子不高的孩子看的。",
            "放低层方便小朋友自己拿书，也能避免攀爬摔倒。",
        ],
        "theme": "公共服务与体贴设计",
        "trick": "按儿童身高与安全优先摆放书架",
    },
    {
        "content_level": "all_ages",
        "title": "提前到站的列车",
        "surface": "小周坐火车去外地开会，车厢广播宣布晚点了二十分钟，但小周下车一看手机时间，反而比自己计划的早到了四十分钟。为什么？",
        "solution": "这趟列车跨越了时区。列车虽然实际运行晚点了二十分钟，但目的地比出发地晚一个小时，手机自动切换当地时区后，显示时间反而比原定当地时间早了四十分钟。",
        "key_facts": [
            "列车确实实际晚点了二十分钟",
            "旅途中跨越了不同时区",
            "目的地时间比出发地慢一小时，抵消晚点后早了四十分钟",
        ],
        "acceptable_variants": ["夏令时回拨一小时导致当地时间提前也可视为等价"],
        "hints": [
            "火车广播并没有播报错误。",
            "小周的手机没有坏，也没有手动改错时间。",
            "六十分钟与二十分钟的差值正好是四十分钟。",
            "出发地和目的地使用的是不同的标准时间。",
            "列车跨越了时区（或遇夏令时回拨），当地时间慢了一小时。",
        ],
        "theme": "出行与时间",
        "trick": "跨时区一小时抵消了二十分钟晚点",
    },
    {
        "content_level": "normal",
        "title": "每天准时的空椅子",
        "surface": "餐馆每天打烊前都会摆出一把空椅子。某天店员忘了摆，老板立刻报警，警方也认为他的决定合理。为什么？",
        "solution": "餐馆与附近独居老人约定：老人每天散步时会把门口的折叠椅搬到指定位置，表示自己平安；店员打烊前再把椅子收回。那天椅子仍在店内，说明老人没有按约出现。老板联系不上老人后报警，警方上门发现老人突发疾病并及时救助。",
        "key_facts": [
            "空椅子是约定好的平安信号",
            "平时由附近老人移动椅子",
            "当天信号没有出现且老人失联",
            "报警目的是确认并救助老人",
        ],
        "acceptable_variants": ["其他通过日常物品确认独居者平安的约定可视为等价"],
        "hints": [
            "椅子不是为顾客准备的。",
            "它的位置代表某个人当天是否出现。",
            "其实平时不是店员在摆椅子，而是某位邻居留下的记号。",
            "那天店员以为忘了摆，实际上是约定的人一直没来。",
            "这是餐馆与独居老人约定的平安信号，失联后老板报警救了老人。",
        ],
        "theme": "社区与平安确认",
        "trick": "空椅子是一种非语言信号",
    },
    {
        "content_level": "normal",
        "title": "熄灭的蜡烛与警笛",
        "surface": "一栋老宅半夜突然停电，屋内的人点起蜡烛后却立刻吹灭并逃出屋外报警。几分钟后消防车呼啸而来，救了整栋楼。为什么？",
        "solution": "屋内发生了严重的燃气泄漏。住户在黑暗中划火柴点蜡烛的瞬间闻到了刺鼻的煤气味（或察觉明火异常窜动），立刻意识到明火会引爆泄漏燃气，于是马上吹灭蜡烛、不碰电器开关，迅速撤离并到屋外报警求助消防。",
        "key_facts": [
            "屋内存在可燃气体或燃气泄漏",
            "点起蜡烛后住户意识到明火会引发爆炸",
            "住户吹灭明火并撤离到安全处报警求助消防",
        ],
        "acceptable_variants": ["液化气或天然气泄漏、担心引爆的合理表述可视为等价"],
        "hints": [
            "停电并不是引发危险的唯一原因。",
            "蜡烛的火焰在当时非常危险。",
            "空气中混入了看不见但极易燃的气体。",
            "消防车赶来是为了处理泄漏和防爆，而不是救火。",
            "屋内发生燃气泄漏，住户发现后急忙灭掉明火逃离并报警。",
        ],
        "theme": "居家安全与应急",
        "trick": "停电点蜡烛反而暴露了燃气泄漏风险",
    },
    {
        "content_level": "normal",
        "title": "写满错字的求救信",
        "surface": "一名银行职员收到客户递来的一张取款单，上面的金额和签名都写得乱七八糟、错字连篇。职员没有退回重写，反而立刻悄悄按下了警报。为什么？",
        "solution": "客户其实正遭人挟持取款，无法当面呼救。客户故意在取款单上把平时熟识的签名和备注写错，并在错字中藏入“报警/救我”等求救暗号，细心的柜员识破异常后不动声色按下了无声警报。",
        "key_facts": [
            "客户处于被胁迫或危险境地",
            "取款单上的异常错字是有意留下的求救信号",
            "柜员看懂异常后暗中触发无声警报保护客户",
        ],
        "acceptable_variants": ["诈骗胁迫、尾随威胁或通过特定暗号求救均可视为等价"],
        "hints": [
            "客户并非文化水平低或喝醉了。",
            "客户身边或门外有不能直接说出口的危险。",
            "这些错字是故意写给银行职员看的。",
            "职员没有当场戳穿，是为了避免激怒挟持者。",
            "客户被挟持取款，用错字藏入求救信息，职员悄悄报警。",
        ],
        "theme": "危机应变与暗号",
        "trick": "看似失误的错字其实是秘密求救信号",
    },
    {
        "content_level": "normal",
        "title": "沉入水底的钥匙",
        "surface": "潜水教练在训练池底故意扔了一把真钥匙，却告诉学员谁先捡上来就淘汰谁。结果最后一名留在水面上的学员通过了考核。为什么？",
        "solution": "这是一场考验安全规范与抗压纪律的考核。教练事先明确规定未获口令不得擅自下潜或超深下潜；扔钥匙是故意制造诱惑和干扰，凡是贸然下潜去捡的人都违反了安全纪律，唯有遵守规则留在水面的学员通过考核。",
        "key_facts": [
            "这是一场考察纪律或安全规范的测试",
            "扔下钥匙是故意设置的干扰或诱惑",
            "擅自下潜违反了安全指令，遵守规则的人通过考核",
        ],
        "acceptable_variants": ["考察潜伴制度、听从口令或冷静判断均可等价"],
        "hints": [
            "钥匙本身并没有机关或危险。",
            "考核的重点不是潜水速度。",
            "下水去捡钥匙的行为本身违反了某条规则。",
            "教练在测试学员面对诱惑时是否严格遵守安全指令。",
            "未获允许不得擅自下潜，坚持守规留在水面的学员过关。",
        ],
        "theme": "职业训练与规则",
        "trick": "把安全纪律考核误解为速度竞赛",
    },
    {
        "content_level": "unrestricted",
        "title": "永远停在三楼的电梯",
        "surface": "老旧公寓的电梯每天凌晨两点都会独自停在三楼并打开门，但三楼已经空置了十年。新搬来的侦探查明真相后，第二天就找到了失踪案的关键证物。为什么？",
        "solution": "公寓的电梯控制箱曾被十年前作案的嫌疑人动过手脚，用来在凌晨定时停靠空置的三楼，方便将装有证物的暗格藏在电梯井道三楼夹层中。定时电路一直没被拆除，侦探顺着电梯每晚的异常停靠检查三楼井道，找到了尘封十年的证物。",
        "key_facts": [
            "电梯凌晨停靠并非灵异现象，而是人为改装的定时装置所致",
            "三楼空置房间或对应井道曾被嫌疑人用来藏匿证物",
            "侦探顺着机械定时线索在三楼井道夹层找到了失踪案证物",
        ],
        "acceptable_variants": ["嫌疑人用定时控制电路隐藏暗格或藏匿赃物/证物均可视为等价"],
        "hints": [
            "这件事没有任何超自然因素。",
            "电梯按固定时间停靠，说明有某种程序或电路在起作用。",
            "这个异常和十年前的失踪案直接相关。",
            "有人曾利用空置的三楼和电梯井道隐藏东西。",
            "嫌疑人改装的定时停靠电路暴露了藏在三楼电梯井夹层的证物。",
        ],
        "theme": "陈年悬案与机械线索",
        "trick": "看似诡异的定时停梯实为旧案留下的机械定时装置",
    },
    {
        "content_level": "unrestricted",
        "title": "没有脚印的雪地",
        "surface": "暴雪后的清晨，山间木屋主人发现院子中央多了一封写给自己的信，但四周齐膝深的雪地里却没有一行进出院子的脚印。他读完信后脸色大变。为什么？",
        "solution": "这封信并不是半夜有人走进院子放的，而是绑在重物上从木屋屋顶或山坡高处抛落（或用无人机投放）到院子中央的；更令主人震惊的是，信中精准描述了他昨晚拉上窗帘前在屋内的举动，说明监视者就在附近高处或屋顶上。",
        "key_facts": [
            "信不是通过步行走进院子放置的，而是从高处抛投或空中投放",
            "因此雪地四周没有进出院子的脚印",
            "信的内容暴露了主人在附近高处被暗中监视的事实",
        ],
        "acceptable_variants": ["无人机空投、从高坡掷下或由屋檐滑落并包含监视信息均可等价"],
        "hints": [
            "信确实是在大雪停后才落到院子里的。",
            "送信的人并没有踩进院子的雪地。",
            "信是从空中落到院子中央的。",
            "让主人变色的不只是送信方式，还有信里写的内容。",
            "信由高处或无人机投下，且内容表明有人一直暗中监视着木屋。",
        ],
        "theme": "暴风雪山庄与悬疑",
        "trick": "无脚印来自空中投放，真正的恐惧来自被监视",
    },
)
