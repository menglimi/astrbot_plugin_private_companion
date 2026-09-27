# -*- coding: utf-8 -*-
from .page_api_diagnostics_overview_shared import (
    _multi_persona_page_context,
    logger,
)
from .page_api_diagnostics_overview_shared import logger
from .page_api_diagnostics_overview_part03 import PrivateCompanionPageApiDiagnosticsOverviewPart03Mixin
from .page_api_diagnostics_overview_part02 import PrivateCompanionPageApiDiagnosticsOverviewPart02Mixin
from .page_api_diagnostics_overview_part01 import PrivateCompanionPageApiDiagnosticsOverviewPart01Mixin
class PrivateCompanionPageApiDiagnosticsOverviewMixin(PrivateCompanionPageApiDiagnosticsOverviewPart01Mixin, PrivateCompanionPageApiDiagnosticsOverviewPart02Mixin, PrivateCompanionPageApiDiagnosticsOverviewPart03Mixin):
    """概览与统计域（从 PrivateCompanionPageApiDiagnosticsMixin 拆出）。"""
