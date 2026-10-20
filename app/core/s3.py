import uuid
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, UploadFile, status
from app.core.config import settings

# Initialize boto3 S3 client
s3_client = boto3.client(
    "s3",
    aws_access_key_id=settings.aws_access_key_id.get_secret_value(),
    aws_secret_access_key=settings.aws_secret_access_key.get_secret_value(),
    region_name=settings.aws_region,
)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


async def upload_image_to_s3(file: UploadFile, folder: str = "general") -> str:
    """
    Validates and uploads an image file to S3.
    Returns the public URL of the uploaded image.
    """
    # 1. Validate File Extension
    file_ext = file.filename.split(".")[-1].lower() if file.filename else ""
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid image format. Allowed formats: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    # 2. Read File & Validate Size
    contents = await file.read(MAX_FILE_SIZE + 1)

    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File size exceeds the 5MB limit.",
        )

    if not contents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded file is empty.",
        )

    # 3. Generate Unique Key (filename)
    unique_filename = f"{folder}/{uuid.uuid4().hex}.{file_ext}"

    # 4. Upload to S3
    try:
        s3_client.put_object(
            Bucket=settings.aws_s3_bucket_name,
            Key=unique_filename,
            Body=contents,
            ContentType=file.content_type or f"image/{file_ext}",
        )
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload image to S3: {str(e)}"
        )

    # 5. Construct Public S3 URL
    url = f"https://{settings.aws_s3_bucket_name}.s3.{settings.aws_region}.amazonaws.com/{unique_filename}"
    return url
