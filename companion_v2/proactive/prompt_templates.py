"""Operator-editable expression templates, independent of event and policy imports."""

from string import Formatter


EXPRESSION_INSTRUCTION = """像角色自然加入日常交流时发出的消息：结合 recent_timing、近期对话和角色当下状态判断是接续话头还是另起话题，保留人格的用词和关系距离。
短时间内刚联系过时，不用郑重重开场、不强调刚想到对方或像隔了很久才见；可以顺着刚聊的内容说，也可以像随手发条消息那样带来一个具体的新念头或生活切片。围绕一个由头说完整，可以有主观感受、小吐槽、联想、轻巧的问题或邀请；长短随内容和当下精力变化，不强压成一句，也不为热闹另起无关话题。避免固定开场、机械汇报、泛泛的陪伴保证和服务式结尾。
语意转折处保留自然标点和段落，称呼、事实、数值及因果关系说连贯；消息气泡按本轮分段设置处理，不靠删逗号或堆空格制造口语感。开口尽量就带出实际内容，让首段独立读来也知道你想说什么；语气词、称呼和紧接的问题通常放在一起，一个完整意思不必拆成多个气泡，停顿留给确有转折的地方。"""
NEWS_SHARING_INSTRUCTION = """把新闻当作看到值得分享的内容后想说的一段话。保持当前人格的年龄感、用词和关系距离，挑一个真正吸引你的亮点，说清发生了什么，再给一点具体感想或好奇，不改写成早报清单或缩短的新闻稿。
事实只取本次标题和摘要；想象、疑问用主观语气，不补剧情、背景、数字或结论。轻松内容可以有惊喜，严肃、伤亡和风险消息按内容认真回应；不固定兴奋、卖萌、堆感叹号或每条追问。
亮点和感想有转折时自然分段，一个意思也可以一段说完。保留标点，不拆开数值、事实或紧密相连的解释，不为凑段数加内容；实际气泡遵循本轮分段设置。text 只写聊天正文，准确来源、原日期和链接留到对方追问时再给。"""
LENGTH_INSTRUCTION = ("这次主动正文按当前强度档位控制在 {chars} 字以内；把想说的意思和有内容的感想自然说完整，"
                      "保留必要的解释和段落。无需压成一句，也不为凑长度补充内容。")
NEWS_LENGTH_INSTRUCTION = ("这次新闻分享的全部正文合计控制在 {chars} 字以内；挑一个亮点轻快地说清，"
                           "保留有内容的感想和自然停顿，无需压成一句。不要为凑字数补充事实、话题或追问。")
PROMPT_DEFAULTS = {
    "expression_prompt": EXPRESSION_INSTRUCTION,
    "news_sharing_prompt": NEWS_SHARING_INSTRUCTION,
    "length_prompt": LENGTH_INSTRUCTION,
    "news_length_prompt": NEWS_LENGTH_INSTRUCTION,
}
LENGTH_FIELDS = frozenset(("length_prompt", "news_length_prompt"))


def checked_template(field, value):
    if type(value) is not str or not value.strip() or len(value) > 8000:
        raise ValueError("expression templates require nonempty text within 8000 characters")
    if field in LENGTH_FIELDS:
        variables = []
        for _, name, spec, conversion in Formatter().parse(value):
            if name is not None:
                if name != "chars" or spec or conversion:
                    raise ValueError("length templates only support the plain {chars} variable")
                variables.append(name)
        if not variables:
            raise ValueError("length templates must retain {chars}")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def effective_template(values, field):
    return checked_template(field, values.get(field, PROMPT_DEFAULTS[field]))
