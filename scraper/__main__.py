"""Allow `python3 -m scraper` as a shorthand for `python3 -m scraper.run`."""

import sys

from scraper.run import main

sys.exit(main())
