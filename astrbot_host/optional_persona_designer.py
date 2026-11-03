"""Mount the designer after core routes; partial optional registration is local."""

import logging


logger = logging.getLogger(__name__)


def register_optional_designer(owner, register):
    registered = getattr(owner.controller.context, "registered_web_apis", None)
    snapshot = list(registered) if isinstance(registered, list) else None
    start = len(owner._registered_routes)
    api = None
    try:
        from .persona_designer_api import PersonaDesignerWebAPI
        api = PersonaDesignerWebAPI(owner.controller)
        for path, handler, methods, description in api.routes():
            register(path, handler, methods, description)
            owner._registered_routes.append((path, handler))
        owner._persona_designer = api
        return True
    except Exception as error:
        if snapshot is not None:
            registered[:] = snapshot
            del owner._registered_routes[start:]
        if api is not None:
            try:
                api.close()
            except Exception:
                pass
        # On hosts without an inspectable route list, retain partial optional
        # ownership for normal unload; never unregister a core handler here.
        logger.warning("[CompanionV2] 新的她未加载 error_type=%s", type(error).__name__)
        return False
