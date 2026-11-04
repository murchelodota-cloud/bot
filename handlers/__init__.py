from .common import router as common_router
from .moderation import router as moderation_router
from .court import router as court_router
from .antispam import router as antispam_router, badwords_router
from .profile import router as profile_router
from .extras import router as extras_router
from .music import router as music_router
from .phrases_cmd import router as phrases_router
from .social import router as social_router
from .coins import router as coins_router
from .whoami import router as whoami_router
from .iris import router as iris_router
from .video import router as video_router
from .duel import router as duel_router
from .royal import router as royal_router
from .quotes import router as quotes_router
from .summary import router as summary_router
from .images import router as images_router
from .nominations import router as nominations_router
from .rewards import router as rewards_router

# antispam последним: он перехватывает все текстовые сообщения
routers = [iris_router, common_router, moderation_router, court_router, profile_router, extras_router, music_router, phrases_router, social_router, coins_router, whoami_router, video_router, duel_router, royal_router, quotes_router, summary_router, images_router, nominations_router, rewards_router, badwords_router, antispam_router]
