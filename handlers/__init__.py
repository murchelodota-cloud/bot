from .common import router as common_router
from .moderation import router as moderation_router
from .court import router as court_router
from .antispam import router as antispam_router
from .profile import router as profile_router
from .extras import router as extras_router

# antispam последним: он перехватывает все текстовые сообщения
routers = [common_router, moderation_router, court_router, profile_router, extras_router, antispam_router]
