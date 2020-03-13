"""Classes for managing a revocation registry."""

import logging
import re

from pathlib import Path
from tempfile import gettempdir

from ...config.injection_context import InjectionContext
from ...utils.http import FetchError, fetch_stream
from ...utils.temp import get_temp_dir

from ..error import RevocationError
import hashlib
import base58

LOGGER = logging.getLogger(__name__)


class RevocationRegistry:
    """Manage a revocation registry and tails file."""

    def __init__(
        self,
        registry_id: str = None,
        *,
        cred_def_id: str = None,
        issuer_did: str = None,
        max_creds: int = None,
        reg_def_type: str = None,
        tag: str = None,
        tails_local_path: str = None,
        tails_public_uri: str = None,
        tails_hash: str = None,
        reg_def: dict = None,
    ):
        """Initialize the revocation registry instance."""
        self._cred_def_id = cred_def_id
        self._issuer_did = issuer_did
        self._max_creds = max_creds
        self._reg_def_type = reg_def_type
        self._registry_id = registry_id
        self._tag = tag
        self._tails_local_path = tails_local_path
        self._tails_public_uri = tails_public_uri
        self._tails_hash = tails_hash
        self._reg_def = reg_def

    @classmethod
    def from_definition(
        cls, revoc_reg_def: dict, public_def: bool
    ) -> "RevocationRegistry":
        """Initialize a revocation registry instance from a definition."""
        reg_id = revoc_reg_def["id"]
        tails_location = revoc_reg_def["value"]["tailsLocation"]
        issuer_did_match = re.match(r"^.*?([^:]*):3:CL:.*", revoc_reg_def["credDefId"])
        issuer_did = issuer_did_match.group(1) if issuer_did_match else None
        init = {
            "cred_def_id": revoc_reg_def["credDefId"],
            "issuer_did": issuer_did,
            "reg_def_type": revoc_reg_def["revocDefType"],
            "max_creds": revoc_reg_def["value"]["maxCredNum"],
            "tag": revoc_reg_def["tag"],
            "tails_hash": revoc_reg_def["value"]["tailsHash"],
            "reg_def": revoc_reg_def,
        }
        if public_def:
            init["tails_public_uri"] = tails_location
        else:
            init["tails_local_path"] = tails_location

        # currently ignored - definition version, public keys
        return cls(reg_id, **init)

    @classmethod
    def get_temp_dir(cls) -> str:
        """Accessor for the temp directory."""
        return get_temp_dir("revoc")

    @property
    def cred_def_id(self) -> str:
        """Accessor for the credential definition ID."""
        return self._cred_def_id

    @property
    def issuer_did(self) -> str:
        """Accessor for the issuer DID."""
        return self._issuer_did

    @property
    def max_creds(self) -> int:
        """Accessor for the maximum number of issued credentials."""
        return self._max_creds

    @property
    def reg_def_type(self) -> str:
        """Accessor for the revocation registry type."""
        return self._reg_def_type

    @property
    def reg_def(self) -> dict:
        """Accessor for the revocation registry definition."""
        return self._reg_def

    @property
    def registry_id(self) -> str:
        """Accessor for the revocation registry ID."""
        return self._registry_id

    @property
    def tag(self) -> str:
        """Accessor for the tag part of the revoc. reg. ID."""
        return self._tag

    @property
    def tails_hash(self) -> str:
        """Accessor for the tails file hash."""
        return self._tails_hash

    @property
    def tails_local_path(self) -> str:
        """Accessor for the tails file local path."""
        return self._tails_local_path

    @tails_local_path.setter
    def tails_local_path(self, new_path: str):
        """Setter for the tails file local path."""
        self._tails_local_path = new_path

    @property
    def tails_public_uri(self) -> str:
        """Accessor for the tails file public URI."""
        return self._tails_public_uri

    @tails_public_uri.setter
    def tails_public_uri(self, new_uri: str):
        """Setter for the tails file public URI."""
        self._tails_public_uri = new_uri

    def get_receiving_tails_local_path(self, context: InjectionContext):
        """Make the local path to the tails file we download from remote URI."""
        if self._tails_local_path:
            return self._tails_local_path

        tails_file_dir = context.settings.get(
            "holder.revocation.tails_files.path",
            Path(gettempdir(), "indy", "revocation", "tails_files"),
        )
        return str(Path(tails_file_dir).joinpath(self._tails_hash))

    def has_local_tails_file(self, context: InjectionContext) -> bool:
        """Test if the tails file exists locally."""
        tails_file_path = Path(self.get_receiving_tails_local_path(context))
        return tails_file_path.is_file()

    async def retrieve_tails(self, context: InjectionContext):
        """Fetch the tails file from the public URI."""
        if not self._tails_public_uri:
            raise RevocationError("Tails file public URI is empty")

        LOGGER.info(
            "Downloading the tails file for the revocation registry: %s",
            self.registry_id,
        )

        try:
            tails_stream = await fetch_stream(self._tails_public_uri)
        except FetchError as e:
            raise RevocationError("Error retrieving tails file") from e

        tails_file_path = Path(self.get_receiving_tails_local_path(context))
        tails_file_dir = tails_file_path.parent
        if not tails_file_dir.exists():
            tails_file_dir.mkdir(parents=True)

        buffer_size = 65536  # should be multiple of 32 bytes for sha256
        with open(tails_file_path, "wb", buffer_size) as tails_file:
            file_hasher = hashlib.sha256()
            buf = await tails_stream.read(buffer_size)
            while len(buf) > 0:
                file_hasher.update(buf)
                tails_file.write(buf)
                buf = await tails_stream.read(buffer_size)

            download_tails_hash = base58.b58encode(file_hasher.digest()).decode("utf-8")
            if download_tails_hash != self.tails_hash:
                raise RevocationError(
                    "The hash of the downloaded tails file does not match."
                )

        self.tails_local_path = tails_file_path
        return self.tails_local_path

    async def get_or_fetch_local_tails_path(self, context: InjectionContext):
        """Get the local tails path, retrieving from the remote if necessary."""
        tails_file_path = self.get_receiving_tails_local_path(context)
        if Path(tails_file_path).is_file():
            return tails_file_path
        return await self.retrieve_tails(context)

    def __repr__(self) -> str:
        """Return a human readable representation of this class."""
        items = ("{}={}".format(k, repr(v)) for k, v in self.__dict__.items())
        return "<{}({})>".format(self.__class__.__name__, ", ".join(items))