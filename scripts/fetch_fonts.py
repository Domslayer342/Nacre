#!/usr/bin/env python3
"""Download Nacre's fonts (Rubik, Material Symbols Rounded) into nacre/fonts. Nacre loads them at startup."""
import os
import sys
import urllib.request

FONTS = {
    "Rubik[wght].ttf": "https://github.com/google/fonts/raw/main/ofl/rubik/Rubik%5Bwght%5D.ttf",
    "MaterialSymbolsRounded[FILL,GRAD,opsz,wght].ttf":
        "https://github.com/google/material-design-icons/raw/master/variablefont/"
        "MaterialSymbolsRounded%5BFILL,GRAD,opsz,wght%5D.ttf",
}


def main():
    dest = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nacre", "fonts")
    os.makedirs(dest, exist_ok=True)
    for name, url in FONTS.items():
        path = os.path.join(dest, name)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            print(f"  have {name}")
            continue
        print(f"  downloading {name}…")
        tmp = path + ".part"
        urllib.request.urlretrieve(url, tmp)
        os.replace(tmp, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
