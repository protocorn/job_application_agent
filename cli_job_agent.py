"""
Legacy CLI entrypoint kept for backward compatibility.

This wrapper routes to the maintained Launchway CLI implementation.
"""

from launchway.cli.agent import main


if __name__ == "__main__":
    main()
