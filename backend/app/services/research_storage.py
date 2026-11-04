"""Private, immutable local photo storage. Paths never come from the client."""
import base64
import hashlib
from io import BytesIO
from pathlib import Path
from uuid import UUID
import warnings

from fastapi import HTTPException
from app.core.config import settings


def decode_photo(value: str) -> tuple[bytes, str]:
    from PIL import Image, UnidentifiedImageError
    try:
        raw = base64.b64decode(value.split(',', 1)[-1], validate=True)
        if not raw or len(raw) > 12 * 1024 * 1024:
            raise ValueError()
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as im:
                if im.format not in ('JPEG', 'PNG', 'WEBP') or im.width * im.height > 40000000:
                    raise ValueError()
                mime = Image.MIME[im.format]
                im.verify()
        return raw, mime
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422, 'Нужно фото JPEG, PNG или WebP до 12 МБ и 40 мегапикселей.') from None


def photo_path(key: str) -> Path:
    # key is a UUID generated/validated by the API, not an arbitrary path.
    name = str(UUID(key))
    return Path(settings.research_storage_dir).resolve() / name


def store_photo(photo_id: UUID, raw: bytes) -> str:
    path = photo_path(str(photo_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as target:
            target.write(raw)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(raw).digest():
            raise HTTPException(409, 'Идентификатор фото уже использован.') from None
    return path.name


def prepared_image(key: str) -> str:
    from PIL import Image, ImageOps
    with Image.open(photo_path(key)) as im:
        prepared = ImageOps.exif_transpose(im).convert('RGB')
        prepared.thumbnail((3000, 3000))
        output = BytesIO()
        prepared.save(output, format='JPEG', quality=95)
    return base64.b64encode(output.getvalue()).decode('ascii')
