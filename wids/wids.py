import base64
import gzip
import hashlib
import io
import os
import random
import re
import sqlite3
import sys
from functools import partial
from typing import Any, BinaryIO, Dict, Optional, TypeVar, Union
from urllib.parse import quote, urlparse

import numpy as np

from .wids_dl import DirectoryCleanup, download_and_open
from .wids_lru import LRUCache
from .wids_mmtar import MMIndexedTar
from .wids_specs import load_dsdesc_and_resolve, urldir
from .wids_tar import TarFileReader, find_index_file

try:
    from torch.utils.data import Dataset, Sampler
except ImportError:

    class Dataset:
        pass

    class Sampler:
        pass


T = TypeVar("T")


def compute_file_md5sum(fname: Union[str, BinaryIO], chunksize: int = 1000000) -> str:
    """Compute the md5sum of a file in chunks.

    Parameters
    ----------
    fname : Union[str, BinaryIO]
        Filename or file object
    chunksize : int, optional
        Chunk size in bytes, by default 1000000

    Returns
    -------
    str
        MD5 sum of the file

    Examples
    --------
    >>> compute_file_md5sum("test.txt")
    'd41d8cd98f00b204e9800998ecf8427e'
    """
    md5 = hashlib.md5()
    if isinstance(fname, str):
        with open(fname, "rb") as f:
            for chunk in iter(lambda: f.read(chunksize), b""):
                md5.update(chunk)
    else:
        fname.seek(0)
        for chunk in iter(lambda: fname.read(chunksize), b""):
            md5.update(chunk)
    return md5.hexdigest()


def compute_file_md5sum(fname: Union[str, BinaryIO], chunksize: int = 1000000) -> str:
    """Compute the md5sum of a file in chunks."""
    md5 = hashlib.md5()
    if isinstance(fname, str):
        with open(fname, "rb") as f:
            for chunk in iter(lambda: f.read(chunksize), b""):
                md5.update(chunk)
    else:
        fname.seek(0)
        for chunk in iter(lambda: fname.read(chunksize), b""):
            md5.update(chunk)
    return md5.hexdigest()


def compute_num_samples(fname):
    ds = IndexedTarSamples(fname)
    return len(ds)


def splitname(fname):
    """Returns the basename and extension of a filename"""
    assert "." in fname, "Filename must have an extension"
    basename, extension = re.match(r"^((?:.*/)?.*?)(\..*)$", fname).groups()
    return basename, extension


def group_by_key(names):
    """Group the file names by key.

    Args:
        names: A list of file names.

    Returns:
        A list of lists of indices, where each sublist contains indices of files
        with the same key.
    """
    groups = []
    last_key = None
    current = []
    for i, fname in enumerate(names):
        # Ignore files that are not in a subdirectory.
        if "." not in fname:
            print(f"Warning: Ignoring file {fname} (no '.')")
            continue
        key, ext = splitname(fname)
        if key != last_key:
            if current:
                groups.append(current)
            current = []
            last_key = key
        current.append(i)
    if current:
        groups.append(current)
    return groups


def default_decoder(sample: Dict[str, Any], format: Optional[Union[bool, str]] = True):
    """A default decoder for webdataset.

    This handles common file extensions: .txt, .cls, .cls2,
        .jpg, .png, .json, .npy, .mp, .pt, .pth, .pickle, .pkl.
    These are the most common extensions used in webdataset.
    For other extensions, users can provide their own decoder.

    Args:
        sample: sample, modified in place
    """
    sample = dict(sample)
    for key, stream in sample.items():
        extensions = key.split(".")
        if len(extensions) < 1:
            continue
        extension = extensions[-1]
        if extension in ["gz"]:
            decompressed = gzip.decompress(stream.read())
            stream = io.BytesIO(decompressed)
            if len(extensions) < 2:
                sample[key] = stream
                continue
            extension = extensions[-2]
        if key.startswith("__"):
            continue
        elif extension in ["txt", "text"]:
            value = stream.read()
            sample[key] = value.decode("utf-8")
        elif extension in ["cls", "cls2"]:
            value = stream.read()
            sample[key] = int(value.decode("utf-8"))
        elif extension in ["jpg", "png", "ppm", "pgm", "pbm", "pnm"]:
            if format == "PIL":
                import PIL.Image

                sample[key] = PIL.Image.open(stream)
            elif format == "numpy":
                import numpy as np

                sample[key] = np.asarray(PIL.Image.open(stream))
            else:
                raise ValueError(f"Unknown format: {format}")
        elif extension == "json":
            import json

            value = stream.read()
            sample[key] = json.loads(value)
        elif extension == "npy":
            import numpy as np

            sample[key] = np.load(stream)
        elif extension == "mp":
            import msgpack

            value = stream.read()
            sample[key] = msgpack.unpackb(value, raw=False)
        elif extension in ["pt", "pth"]:
            import torch

            sample[key] = torch.load(stream)
        elif extension in ["pickle", "pkl"]:
            import pickle

            sample[key] = pickle.load(stream)
    return sample


class IndexedTarSamples:
    """A class that accesses samples in a tar file. The tar file must follow
    WebDataset conventions. The tar file is indexed when the IndexedTarSamples
    object is created. The samples are accessed by index using the __getitem__
    method. The __getitem__ method returns a dictionary containing the files
    for the sample. The key for each file is the extension of the file name.
    The key "__key__" is reserved for the key of the sample (the basename of
    each file without the extension). For example, if the tar file contains
    the files "sample1.jpg" and "sample1.txt", then the sample with key
    "sample1" will be returned as the dictionary {"jpg": ..., "txt": ...}.
    """

    def __init__(
        self,
        tar_file,
        *,
        path=None,
        md5sum=None,
        expected_size=None,
        source=None,
        use_mmap=True,
        index_file=find_index_file,
    ):
        # Create TarFileReader object to read from tar_file
        self.source = source
        self.path = path or tar_file
        assert isinstance(
            self.path, str
        ), f"specify path= if the tar_file is a stream, got {path}, {tar_file}"
        if use_mmap:
            self.reader = MMIndexedTar(tar_file)
        else:
            self.reader = TarFileReader(tar_file, index_file=index_file)
        # Get list of all files in tar_file
        all_files = self.reader.names()
        # Group files by key into samples
        self.samples = group_by_key(all_files)
        if md5sum is not None:
            got = compute_file_md5sum(tar_file)
            assert got == md5sum, f"MD5 sum mismatch: expected {md5sum}, got {got}"
        if expected_size is not None:
            assert (
                len(self) == expected_size
            ), f"Expected {expected_size} samples, got {len(self)}"

    def __len__(self):
        return len(self.samples)

    def close(self):
        self.reader.close()

    def __getitem__(self, idx):
        # Get indexes of files for the sample at index idx
        indexes = self.samples[idx]
        sample = {}
        key = None
        for i in indexes:
            # Get filename and data for the file at index i
            fname, data = self.reader.get_file(i)
            # Split filename into key and extension
            k, ext = splitname(fname)
            # Make sure all files in sample have same key
            key = key or k
            assert key == k
            sample[ext] = data
        # Add key to sample
        sample["__key__"] = key
        return sample


def hash_localname(dldir="/tmp/_wids_cache"):
    os.makedirs(dldir, exist_ok=True)

    connection = sqlite3.connect(os.path.join(dldir, "cache.db"))
    cursor = connection.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS cache (url TEXT PRIMARY KEY, path TEXT, checksum TEXT)"
    )
    connection.commit()

    def f(shard):
        """Given a URL, return a local name for the shard."""
        if shard.startswith("pipe:"):
            # uuencode the entire URL string
            hex32 = base64.urlsafe_b64encode(hashlib.sha256(shard.encode()).digest())[
                :32
            ].decode()
            return os.path.join(dldir, "pipe__" + hex32)
        else:
            # we hash the host and directory components into a 16 character string
            dirname = urldir(shard)
            hex16 = base64.urlsafe_b64encode(hashlib.sha256(dirname.encode()).digest())[
                :16
            ].decode()
            # the cache name is the concatenation of the hex16 string and the file name component of the URL
            cachename = "data__" + hex16 + "__" + os.path.basename(urlparse(shard).path)
            checksum = None
            cursor.execute(
                "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                (shard, cachename, checksum),
            )
            connection.commit()
            return os.path.join(dldir, cachename)

    return f


def cache_localname(cachedir):
    os.makedirs(cachedir, exist_ok=True)

    def f(shard):
        """Given a URL, return a local name for the shard."""
        path = urlparse(shard).path
        fname = os.path.basename(path)
        return os.path.join(cachedir, fname)

    return f


def default_localname(dldir="/tmp/_wids_cache"):
    os.makedirs(dldir, exist_ok=True)

    def f(shard):
        """Given a URL, return a local name for the shard."""
        cachename = quote(shard, safe="+-")
        return os.path.join(dldir, cachename)

    return f


class LRUShards:
    """A class that manages a cache of shards. The cache is a LRU cache that
    stores the local names of the shards as keys and the downloaded paths as
    values. The shards are downloaded to a directory specified by dldir.
    The local name of a shard is computed by the localname function, which
    takes the shard URL as an argument. If keep is True, the downloaded files
    are not deleted when they are no longer needed.
    """

    def __init__(self, num_shards, keep=False, localname=default_localname()):
        self.localname = localname
        # the cache contains the local name as the key and the downloaded path as the value
        self.lru = LRUCache(num_shards, release_handler=self.release_handler)
        # keep statistics
        self.reset_stats()

    def reset_stats(self):
        self.accesses = 0
        self.misses = 0

    def __len__(self):
        return len(self.lru)

    def release_handler(self, key, value):
        value.close()

    def clear(self):
        self.lru.clear()

    def get_shard(self, url):
        assert isinstance(url, str)
        self.accesses += 1
        if url not in self.lru:
            local = self.localname(url)
            with download_and_open(url, local) as stream:
                itf = IndexedTarSamples(stream, source=url, path=local)
            self.lru[url] = itf
            self.misses += 1
            self.last_missed = True
        else:
            self.last_missed = False
        return self.lru[url]


def interpret_transformations(transformations):
    """Interpret the transformations argument.

    This takes care of transformations specified as string shortcuts
    and returns a list of callables.
    """
    if not isinstance(transformations, list):
        transformations = [transformations]

    result = []

    for transformation in transformations:
        if transformation == "PIL":
            transformation = partial(default_decoder, format="PIL")
        elif transformation == "numpy":
            transformation = partial(default_decoder, format="numpy")
        else:
            assert callable(transformation)
        result.append(transformation)

    return result


def hash_dataset_name(input_string):
    """Compute a hash of the input string and return the first 16 characters of the hash."""
    # Compute SHA256 hash of the input string
    hash_object = hashlib.sha256(input_string.encode())
    hash_digest = hash_object.digest()

    # Encode the hash in base64
    base64_encoded_hash = base64.urlsafe_b64encode(hash_digest)

    # Return the first 16 characters of the base64-encoded hash
    return base64_encoded_hash[:16].decode("ascii")


class ShardListDataset(Dataset[T]):
    """An indexable dataset based on a list of shards.

    The dataset is either given as a list of shards with optional options and name,
    or as a URL pointing to a JSON descriptor file.

    Datasets can reference other datasets via `source_url`.

    Shard references within a dataset are resolve relative to an explicitly
    given `base` property, or relative to the URL from which the dataset
    descriptor was loaded.
    """

    def __init__(
        self,
        shards,
        cache_size=int(1e12),
        cache_dir=None,
        cache_cleanup=10,
        dataset_name=None,
        localname=None,
        transformations="PIL",
        keep=False,
        base=None,
        options=None,
    ):
        """Create a ShardListDataset.

        Args:
            shards: a list of (filename, length) pairs or a URL pointing to a JSON descriptor file
            cache_size: the number of shards to keep in the cache
            localname: a function that maps URLs to local filenames
        """
        if options is None:
            options = {}
        super(ShardListDataset, self).__init__()
        # shards is a list of (filename, length) pairs. We'll need to
        # keep track of the lengths and cumulative lengths to know how
        # to map indices to shards and indices within shards.
        if isinstance(shards, (str, io.IOBase)):
            if base is None and isinstance(shards, str):
                base = urldir(shards)
            self.base = base
            self.spec = load_dsdesc_and_resolve(shards, options=options, base=base)
            self.shards = self.spec.get("shardlist", [])
            self.dataset_name = self.spec.get("name") or hash_dataset_name(str(shards))
        else:
            self.base = None
            self.spec = options
            self.shards = shards
            self.dataset_name = dataset_name or hash_dataset_name(str(shards))

        self.lengths = [shard["nsamples"] for shard in self.shards]
        self.cum_lengths = np.cumsum(self.lengths)
        self.total_length = self.cum_lengths[-1]

        if cache_dir is not None:
            # when a cache dir is explicitly given, we download files into
            # that directory without any changes
            self.cache_dir = cache_dir
            self.localname = cache_localname(cache_dir)
        elif localname is not None:
            # when a localname function is given, we use that
            self.cache_dir = None
            self.localname = localname
        else:
            # when no cache dir or localname are given, use the cache from the environment
            self.cache_dir = os.environ.get("WIDS_CACHE", "/tmp/_wids_cache")
            self.localname = default_localname(self.cache_dir)

        if True or int(os.environ.get("WIDS_VERBOSE", 0)):
            nbytes = sum(shard.get("filesize", 0) for shard in self.shards)
            nsamples = sum(shard["nsamples"] for shard in self.shards)
            print(
                str(shards)[:50],
                "base:",
                self.base,
                "name:",
                self.spec.get("name"),
                "nfiles:",
                len(self.shards),
                "nbytes:",
                nbytes,
                "samples:",
                nsamples,
                "cache:",
                self.cache_dir,
                file=sys.stderr,
            )
        self.transformations = interpret_transformations(transformations)

        self.cache = LRUShards(cache_size, localname=self.localname, keep=keep)

        self.cleanup = None
        if cache_cleanup is not None and self.cache_dir is not None:
            self.cleanup = DirectoryCleanup(
                self.cache_dir, every=cache_cleanup, maxsize=cache_size
            )

    def add_transform(self, transform):
        """Add a transformation to the dataset."""
        self.transformations.append(transform)
        return self

    def __len__(self):
        """Return the total number of samples in the dataset."""
        return self.total_length

    def get_stats(self):
        """Return the number of cache accesses and misses."""
        return self.cache.accesses, self.cache.misses

    def check_cache_misses(self):
        """Check if the cache miss rate is too high."""
        accesses, misses = self.get_stats()
        if accesses > 100 and misses / accesses > 0.3:
            # output a warning only once
            self.check_cache_misses = lambda: None
            print(
                "Warning: ShardListDataset has a cache miss rate of {:.1%}%".format(
                    misses * 100.0 / accesses
                )
            )

    def get_shard(self, index):
        """Get the shard and index within the shard corresponding to the given index."""
        # Find the shard corresponding to the given index.
        shard_idx = np.searchsorted(self.cum_lengths, index, side="right")

        # Figure out which index within the shard corresponds to the
        # given index.
        if shard_idx == 0:
            inner_idx = index
        else:
            inner_idx = index - self.cum_lengths[shard_idx - 1]

        # Get the shard and return the corresponding element.
        desc = self.shards[shard_idx]
        url = desc["url"]
        shard = self.cache.get_shard(url)
        if self.cache.last_missed and self.cleanup is not None:
            self.cleanup.run_cleanup()
        return shard, inner_idx, desc

    def __getitem__(self, index):
        """Return the sample corresponding to the given index."""
        shard, inner_idx, desc = self.get_shard(index)
        sample = shard[inner_idx]

        # Check if we're missing the cache too often.
        self.check_cache_misses()

        sample["__dataset__"] = desc.get("dataset")
        sample["__index__"] = index
        sample["__shard__"] = desc["url"]
        sample["__shardindex__"] = inner_idx

        # Apply transformations
        for transform in self.transformations:
            sample = transform(sample)

        return sample

    def close(self):
        """Close the dataset."""
        self.cache.clear()


class ShardListSampler(Sampler):
    """A sampler that samples consistent with a ShardListDataset.

    This sampler is used to sample from a ShardListDataset in a way that
    preserves locality.

    This returns a permutation of the indexes by shard, then a permutation of
    indexes within each shard. This ensures that the data is accessed in a
    way that preserves locality.

    Note that how this ends up splitting data between multiple workers ends up
    on the details of the DataLoader. Generally, it will likely load samples from the
    same shard in each worker.

    Other more sophisticated shard-aware samplers are possible and will likely
    be added.
    """

    def __init__(self, dataset, *, lengths=None, seed=0, shufflefirst=False):
        if lengths is None:
            lengths = list(dataset.lengths)
        self.ranges = []
        start = 0
        for l in lengths:
            self.ranges.append((start, start + l))
            start += l
        self.seed = seed
        self.shufflefirst = shufflefirst
        self.epoch = 0

    def __iter__(self):
        pass

        self.rng = random.Random(self.seed + 1289738273 * self.epoch)
        shardperm = list(range(len(self.ranges)))
        if self.epoch > 0 or self.shufflefirst:
            # usually, we don't shuffle shards in epoch 0 to achieve
            # fast startup during testing
            self.rng.shuffle(shardperm)
        for shard in shardperm:
            start, end = self.ranges[shard]
            indexes = list(range(start, end))
            self.rng.shuffle(indexes)
            yield from indexes
        self.epoch += 1


ShardedSampler = ShardListSampler
