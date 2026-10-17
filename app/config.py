from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "D.A.M.N Booking Hub"
    MONGODB_URL: str = "mongodb://localhost:27017"
    DATABASE_NAME: str = "damn_booking_db"
    BASE_WEBHOOK_URL: str
    PARENT_BOT_TOKEN: str = ""
    PADDLE_WEBHOOK_SECRET_KEY: str = "paddle_secret_here"
    SECRET_KEY: str = "super_secret_jwt_key_change_me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    class Config:
        env_file = ".env"

settings = Settings()