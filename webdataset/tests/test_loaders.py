from torch.utils.data import DataLoader

import webdataset as wds

from webdataset.tests.testconfig import *


def test_webloader():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.split_by_worker,
        wds.tarfile_to_samples(),
        wds.to_tuple("png;jpg", "cls"),
    )
    dl = DataLoader(ds, num_workers=4, batch_size=3)
    nsamples = count_samples_tuple(dl)
    assert nsamples == (47 + 2) // 3, nsamples


def test_webloader2():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.split_by_worker,
        wds.tarfile_to_samples(),
        wds.to_tuple("png;jpg", "cls"),
    )
    dl = wds.DataPipeline(
        DataLoader(ds, num_workers=4, batch_size=3, drop_last=True),
        wds.unbatched(),
    )
    nsamples = count_samples_tuple(dl)
    assert nsamples == 45, nsamples


def test_dataloader():
    import torch

    ds = wds.WebDataset(remote_loc + remote_shards)
    dl = torch.utils.data.DataLoader(ds, num_workers=4)
    assert count_samples_tuple(dl, n=100) == 100


def test_webloader():
    ds = wds.WebDataset(local_data)
    dl = wds.WebLoader(ds, num_workers=4, batch_size=3)
    nsamples = count_samples_tuple(dl)
    assert nsamples == (47 + 2) // 3, nsamples


def test_webloader_repeat():
    ds = wds.WebDataset(local_data)
    dl = wds.WebLoader(ds, num_workers=4, batch_size=3).repeat(nepochs=2)
    nsamples = count_samples_tuple(dl)
    assert nsamples == 2 * (47 + 2) // 3, nsamples


def test_webloader_unbatched():
    ds = wds.WebDataset(local_data).to_tuple("png", "cls")
    dl = wds.WebLoader(ds, num_workers=4, batch_size=3).unbatched()
    nsamples = count_samples_tuple(dl)
    assert nsamples == 47, nsamples