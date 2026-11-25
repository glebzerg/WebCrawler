from contextlib import suppress
from pathlib import Path

import aiofiles.os
import aiofiles.tempfile


def same_destination(first: Path, second: Path) -> bool:
    if first.resolve() == second.resolve():
        return True
    try:
        return first.samefile(second)
    except FileNotFoundError:
        return False


async def write_report(path: Path, content: str) -> None:
    """Replace the report only after the UTF-8 contents have been written."""
    await aiofiles.os.makedirs(path.parent, exist_ok=True)
    temporary = None
    try:
        async with aiofiles.tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            temporary = Path(file.name)
            await file.write(content)
        await aiofiles.os.replace(temporary, path)
    except BaseException:
        if temporary is not None:
            with suppress(FileNotFoundError):
                await aiofiles.os.remove(temporary)
        raise
