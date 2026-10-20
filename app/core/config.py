from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding='utf-8',
        extra="ignore",
    )

    database_url: str

    secret_key: SecretStr
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    reset_token_expire_minutes: int = 60

    mail_server: str
    mail_port: int
    mail_username: str
    mail_password: SecretStr
    mail_from: str
    mail_use_tls: bool
    frontend_url: str

    paystack_secret_key: SecretStr
    paystack_callback_url: str

    # AWS S3 Settings
    aws_access_key_id: SecretStr
    aws_secret_access_key: SecretStr
    aws_region: str = "eu-north-1"
    aws_s3_bucket_name: str

    environment: str = 'development'
    redis_url: str = "redis://localhost:6379/0"


settings = Settings()
