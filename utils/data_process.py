import os, random
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import cv2
import torch
from numpy.typing import NDArray
from torch.utils.data import Dataset, DataLoader

if __name__ == '__main__':
    import sys

    sys.path.append(str(Path(__file__).resolve().parents[1]))

from submodules.UsefulFileTools.FileOperator import get_filenames, str_format
from cross_validation_config import datasets_tr, datasets_test


currentFrDir = 'Data/currentFr'
emptyBgDir = 'Data/emptyBg'
recentBgDir = 'Data/recentBg'


class DatasetConfig:
    next_stage = 5
    frame_groups = 5
    gap_range = [2, 200]
    sample4oneVideo = 200

    def __init__(self, isModel3D=True) -> None:
        self.concat_axis = -1 if isModel3D else 0  # axis dependent by model input_channel


class CDNet2014OneVideo:
    def __init__(self, cate_name: str, name: str) -> None:
        self.name = name

        self.temporalROI: Tuple[int]
        self.gtPaths_all: Tuple[str]
        self.inputPaths_all: Tuple[str]
        self.recentBgPaths_all: Tuple[str]
        self.emptyBgPaths: Tuple[str]

        self.gtPaths_beforeROI = Tuple[str]
        self.inputPaths_beforeROI = Tuple[str]
        self.recentBgPaths_beforeROI = Tuple[str]
        self.gtPaths_inROI = Tuple[str]
        self.inputPaths_inROI = Tuple[str]
        self.recentBgPaths_inROI = Tuple[str]

        with open(file=f'{currentFrDir}/{cate_name}/{self.name}/temporalROI.txt', mode='r') as f:
            self.temporalROI = tuple(map(int, f.read().split(' ')))

        self.__load_paths(cate_name)
        self.__split_by_temporalROI()

    def __load_paths(self, cate_name: str):
        for sub_dir, extension, dir_path, var_name in zip(
            ['groundtruth/', 'input/', '', ''],
            ['png', *['jpg'] * 3],
            [currentFrDir, currentFrDir, emptyBgDir, recentBgDir],
            ['gtPaths_all', 'inputPaths_all', 'emptyBgPaths', 'recentBgPaths_all'],
        ):
            paths = get_filenames(dir_path=f'{dir_path}/{cate_name}/{self.name}/{sub_dir}', specific_name=f'*.{extension}')
            # exclude Synology NAS snapshot
            setattr(self, var_name, tuple(sorted([path for path in paths if '@eaDir' not in path])))

    def __split_by_temporalROI(self):
        for var_beforeROI, var_Mask, paths in zip(
            ['gtPaths_beforeROI', 'inputPaths_beforeROI', 'recentBgPaths_beforeROI'],
            ['gtPaths_inROI', 'inputPaths_inROI', 'recentBgPaths_inROI'],
            [self.gtPaths_all, self.inputPaths_all, self.recentBgPaths_all],
        ):
            setattr(self, var_beforeROI, (*paths[: self.temporalROI[0]], *paths[self.temporalROI[1] :]))
            setattr(self, var_Mask, paths[self.temporalROI[0] : self.temporalROI[1]])

    def __repr__(self) -> str:
        return self.name


class CDNet2014OneCategory:
    def __init__(self, name: str, ls: List[str]) -> None:
        self.name = name
        self.videos = [CDNet2014OneVideo(self.name, video_str) for video_str in ls]

    def __repr__(self) -> str:
        return self.name


class CDNet2014Dataset(Dataset):
    def __init__(
        self,
        cv_dict: Dict[str, Dict[str, List[str]]] = datasets_tr,
        cv_set: int = 0,
        cfg: DatasetConfig = DatasetConfig(),
    ) -> None:
        self.cv_dict = cv_dict[cv_set]  # from cross_validation_config.py
        self.cfg = cfg
        self.gap = self.cfg.gap_range[0]

        self.categories = [CDNet2014OneCategory(name=k, ls=v) for k, v in self.cv_dict.items()]

        gap_steps = self.cfg.gap_range[-1] // self.cfg.next_stage + 1
        self.gap_arr: NDArray[np.int16] = np.linspace(*self.cfg.gap_range, gap_steps, dtype=np.int16)

        self.data_infos: List[Tuple[CDNet2014OneCategory, CDNet2014OneVideo, int]] = []  # [(cate, video, frame_inROI_id)...]
        self.__collect_training_data()

    def __collect_training_data(self):
        sample4oneVideo = self.cfg.sample4oneVideo

        for cate in self.categories:
            for video in cate.videos:
                idxs = sorted(random.sample(range(len(video.inputPaths_inROI)), k=sample4oneVideo))
                self.data_infos = [*self.data_infos, *list(zip([cate] * sample4oneVideo, [video] * sample4oneVideo, idxs))]

    def __getitem__(self, idx: int) -> Any:
        features: torch.Tensor
        frames: np.ndarray
        labels: np.ndarray

        cate, video, frame_id = self.data_infos[idx]

        frame_ids = self.__get_frameIDs(video, frame_id)
        frame_ls = []
        label_ls = []
        for i in frame_ids:
            frame_ls.append(cv2.imread(video.inputPaths_inROI[i], cv2.COLOR_BGR2RGB))
            label_ls.append(cv2.imread(video.gtPaths_inROI[i], cv2.IMREAD_GRAYSCALE))

        frames = np.concatenate(frame_ls, axis=self.cfg.concat_axis)  #! Error: shape is wrong!!
        labels = np.concatenate(label_ls, axis=self.cfg.concat_axis)
        features = self.__get_features(video)

        return features, frames, labels

    # *dataset selecting strategy
    def __get_frameIDs(self, video: CDNet2014OneVideo, start_id: int) -> List[int]:
        len_frame = len(video.inputPaths_inROI)
        if len_frame - start_id < self.cfg.frame_groups * self.gap:
            return sorted(random.sample(range(start_id, len_frame), k=self.cfg.frame_groups))

        frame_ids: List[int] = []
        frame_id = start_id
        for _ in range(self.cfg.frame_groups):
            frame_id += random.randint(1, self.gap + 1)
            frame_ids.append(frame_id)

        return frame_ids

    def __get_features(self, video: CDNet2014OneVideo, mean=0, std=180):
        f0 = cv2.imread(random.choice(video.emptyBgPaths), cv2.COLOR_BGR2RGB)
        f1 = f0 + np.random.normal(0, 180, f0.shape)

        return np.concatenate([f0, f1], axis=self.cfg.concat_axis)

    def next_frame_gap(self, epoch: int = 1):
        self.gap = self.gap_arr[epoch // self.cfg.next_stage]

    def __len__(self):
        return len(self.data_infos)


if __name__ == '__main__':
    dataset = CDNet2014Dataset(cv_dict=datasets_tr, cv_set=5, cfg=DatasetConfig())

    print(dataset.categories[0].name)
    print(dataset.categories[0].videos[0].name)
    print(dataset.categories[0].videos[0].gtPaths_beforeROI[0])

    print(dataset.data_infos)
    print(len(dataset.data_infos))

    a, b, c = iter(dataset)
