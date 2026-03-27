import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    PASEETAH_EMAIL: str = os.getenv("PASEETAH_EMAIL", "a.elashmony@almahafez.com")
    PASEETAH_PASSWORD: str = os.getenv("PASEETAH_PASSWORD", "Almahafez1997")
    CAPTCHA_SOLVER_API_KEY: str = os.getenv("CAPTCHA_SOLVER_API_KEY", "")
    PORT: int = int(os.getenv("PORT", "8000"))
    SESSION_CACHE_FILE: str = os.path.join(
        os.path.dirname(__file__), "session_cache.json"
    )


settings = Settings()
