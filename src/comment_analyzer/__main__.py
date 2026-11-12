"""Entry point: python -m comment_analyzer [link], or the command line: ... analyse <link>."""

import sys

from comment_analyzer.app import main

if __name__ == "__main__":
    sys.exit(main())
