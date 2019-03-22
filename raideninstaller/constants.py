import json
import logging
import pathlib
import sys

import requests


log = logging.getLogger(__name__)

ARCHIVE_EXT = 'zip' if sys.platform == 'darwin' else 'tar.gz'


class PATHS:
    USR_BIN_DIR = pathlib.Path('/usr/local/bin')
    DEFAULT_INSTALL_DIR = pathlib.Path('/opt/raiden')


class NETWORKS:
    MAINNET = 'mainnet'
    KOVAN = 'kovan'
    RINKEBY = 'rinkeby'
    ROPSTEN = 'ropsten'

    TESTNETS = [ROPSTEN, RINKEBY, KOVAN]


class RAIDEN_META:
    NAME = 'raiden'
    VERSION = 'v0.100.2'
    BINARY_NAME = f'raiden-{VERSION}'
    ARCH = 'macOS' if sys.platform == 'darwin' else 'linux'
    ARCHIVE = f'{BINARY_NAME}-{ARCH}-x86_64.{ARCHIVE_EXT}'
    DOWNLOAD_URL = f'https://github.com/raiden-network/raiden/releases/download/{VERSION}/{ARCHIVE}'

    @classmethod
    def update(cls) -> None:
        """Update meta version for Raiden to refer to the latest release, if available."""
        try:
            resp = requests.get('https://api.github.com/repos/raiden-network/raiden/releases')
            LATEST = resp.json()[0]['tag_name']
        except requests.HTTPError as e:
            log. exception(e)
            log.error('Could not retrieve latest release data due to an HTTPError!')
        except json.JSONDecodeError as e:
            log.exception(e)
            log.error('Could not retrieve latest release data! It is not valid JSON!')
        except IndexError as e:
            log.exception(e)
            log.error('Could not retrieve data on latest release - response is an empty list!')
        except KeyError as e:
            log.exception(e)
            log.error('Could not retrieve "tag_name" from response!')
        else:
            cls.VERSION = LATEST
            cls.BINARY_NAME = f'raiden-{LATEST}'
            cls.ARCHIVE = f'{cls.BINARY_NAME}-{cls.ARCH}-x86_64.{ARCHIVE_EXT}'
            cls.DOWNLOAD_URL = f'https://github.com/raiden-network/raiden/releases/download/{LATEST}/{cls.ARCHIVE}'


class GETH_META:
    NAME = 'geth'
    VERSION = '1.8.23'
    ARCH = sys.platform
    COMMIT = 'c9427004'
    DOWNLOAD_URL = f'https://gethstore.blob.core.windows.net/builds/geth-{ARCH}-{VERSION}-{COMMIT}.tar.gz'
    SIGNATURE = f'{DOWNLOAD_URL}.asc'
    BIN_PATH = PATHS.USR_BIN_DIR.joinpath(NAME)
    KEYSTORE_PATH = pathlib.Path.home().joinpath('.ethereum')


class PARITY_META:
    NAME = 'parity'
    VERSION = 'v2.4.0'
    ARCH = 'apple-darwin' if sys.platform == 'darwin' else 'unknown-linux-gnu'
    DOWNLOAD_URL = f'https://releases.parity.io/ethereum/{VERSION}/x86_64-{ARCH}/parity'
    BIN_PATH = PATHS.USR_BIN_DIR.joinpath(NAME)
    KEYSTORE_PATH = pathlib.Path.home().joinpath('.local', 'share', 'io.parity.ethereum', 'keys')


class STRINGS:
    # String names of our installation steps
    STEP_1 = '\nStep 1: Raiden Binary Setup\n'
    STEP_2 = '\nStep 2: Ethereum Client Setup\n'
    STEP_3 = '\nStep 3: Account Setup\n'
    STEP_4 = '\nStep 4: Account Funding\n'
    STEP_5 = '\nStep 5: Token Acquisition\n'

    # User input parser strings
    CHOOSE_ONE_LONG = ''
    CHOOSE_ONE_SHORT = ''
    SELECTION_ACCEPTED = '\nInput accepted. Continuing..'
    SELECTION_REJECTED = '\nInput rejected. Please enter a valid option.'
    SELECTION_CANNOT_BE_EMPTY = '\nInput CANNOT be empty! Try again..'
