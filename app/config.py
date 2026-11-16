
# from pydantic_settings import BaseSettings, SettingsConfigDict


# class Settings(BaseSettings):
#     model_config = SettingsConfigDict(env_file=".env", extra="ignore")

#     DATABASE_URL: str
#     APP_BASE_URL: str = "http://localhost:8000"
#     FRONTEND_URL: str = "http://localhost:3000"
#     CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173,https://uat-skillnext.vercel.app"
#     VERIFICATION_TOKEN_HOURS: int = 24

#     JWT_SECRET: str
#     JWT_ALGORITHM: str = "HS256"
#     ACCESS_TOKEN_MINUTES: int = 60

#     SMTP_HOST: str = ""
#     SMTP_PORT: int = 587
#     SMTP_USER: str = ""
#     SMTP_PASSWORD: str = ""
#     SMTP_FROM: str = "no-reply@skillnext.com"


# settings = Settings()

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    APP_BASE_URL: str = "http://localhost:8000"
    FRONTEND_URL: str = "http://localhost:3000"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173,https://uat-skillnext.vercel.app"
    VERIFICATION_TOKEN_HOURS: int = 24

    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_MINUTES: int = 60

    DRIVE_ROOT_FOLDER_ID: str = ""
    DRIVE_TOKEN_FILE: str = "token.json"
    MAX_VIDEO_SIZE_MB: int = 500
    MAX_DOCUMENT_SIZE_MB: int = 50

    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "no-reply@skillnext.com"


settings = Settings()