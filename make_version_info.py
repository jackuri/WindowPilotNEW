"""Writes file_version_info.txt so the built .exe shows its version in Windows (Properties → Details)."""
from version import __version__

parts = [int(p) for p in __version__.split(".")] + [0]
nums = ", ".join(str(n) for n in parts[:4])
TEMPLATE = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({nums}), prodvers=({nums}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'WindowPilot'),
      StringStruct('FileDescription', 'WindowPilot - AI assistant for Windows app windows'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('ProductVersion', '{__version__}'),
      StringStruct('OriginalFilename', 'WindowPilot-{__version__}.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""

if __name__ == "__main__":
    with open("file_version_info.txt", "w", encoding="utf-8") as fh:
        fh.write(TEMPLATE)
    print(__version__)
