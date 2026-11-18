"""Entry point of the portable build (tools/portable/wwb_scanner.spec).

Same as wwb_scanner-ui; wwb_scanner.ui.launch.run sees the frozen build and
keeps the data next to the exe (wwb_scanner/portable.py)."""
import sys

from wwb_scanner.ui.launch import run

if __name__ == '__main__':
    sys.exit(run())
