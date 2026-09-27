# -*- coding: utf-8 -*-
"""llm_tool_actions_photo_generate_part02 段级拆分的共享哨兵。

`_StageNext` 表示「阶段正常跑完，继续下一阶段」，并携带需要回填给宿主顺序
编排壳的局部名值。它继承 ``tuple``，宿主可直接解包。

设计要点（与 tmp/refactor/dstc_split.py 的 ``return None`` 哨兵不同，这里反过来）：

* 阶段里**原本的 return 一律保持逐字节原样**——带值的 return 返回的就是
  ``_pc_generate_photo_impl`` 的最终结果，宿主收到「非 ``_StageNext`` 的返回值」
  后原样 ``return`` 出去（裸 return 与 ``return None`` 因此天然等价）。
* 每个阶段只在**末尾**追加一条合成语句 ``return _StageNext((...))``，
  把该段之后仍然活跃的局部名交还给宿主。

因此段体与拆分前的原文逐字节相同，语义为零改动。
"""
from __future__ import annotations

__all__ = ["_StageNext"]


class _StageNext(tuple):
    """阶段正常结束、需要继续下一阶段的哨兵（tuple 子类，可直接解包）。"""

    __slots__ = ()
