import os
import sys
import argparse
from tqdm import tqdm

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset
from torch.utils.data import DataLoader

from model_utility import *
from model_option import *
from model_loader import *
from model_layer import *
from model_loss import *



DEVICE   = 'cuda:0' if torch.cuda.is_available() else 'cpu'
testpath = {
    "kitti_eigen_full": "./splits/kitti_eigen_full/test_files.txt",
    "kitti_eigen_zhou": "./splits/kitti_eigen_zhou/test_files.txt"}



def load_weights(args):
    encoder_weights = torch.load(args.encoder_path)
    decoder_weights = torch.load(args.decoder_path)

    model = {}
    model["encoder"] = ResnetEncoder(18, False)
    model["decoder"] = DepthDecoder(model["encoder"].num_ch_enc)

    model["encoder"].load_state_dict(
        {k: v for k, v in encoder_weights.items() if k in model["encoder"].state_dict()})
    model["decoder"].load_state_dict(decoder_weights)
    
    model.update({key: model[key].to(DEVICE) for key in model})
    model.update({key: model[key].eval() for key in model})
    return model



def load_ground_truth(args, lines):
    ground_truth_list = []
    for line in lines:
        folder, frame_id, _ = line.split()
        frame_id            = int(frame_id)
        calibration_path    = os.path.join(args.datapath, folder.split("/")[0])
        velo_filename       = os.path.join(
            args.datapath, folder, "velodyne_points/data", "{:010d}.bin".format(frame_id))
        ground_truth = point2depth(calibration_path, velo_filename, 2, True)
        ground_truth_list.append(ground_truth.astype(np.float32))
    return ground_truth_list



def inference(args):
    MIN_DEPTH = 1e-3
    MAX_DEPTH = 80.0
    filename  = readlines(testpath["kitti_{}".format(args.splits)])
    dataset   = KITTIMonoDataset_v2(args.datapath, filename, False, [0], 192, 640, ".jpg", 4)
    loader    = DataLoader(dataset, batch_size = 1, shuffle = False, drop_last = False)
    print(">>> Testset length {}, Batch iteration {}".format(len(filename), loader.__len__()))

    model = load_weights(args)
    print(">>> Loaded model")

    result_list       = []
    disparity_list    = []
    ground_truth_list = load_ground_truth(args, filename)
    print(">>> Loaded ground truth depth")


    with torch.no_grad():
        for inputs in tqdm(loader):
            outputs      = model["decoder"](model["encoder"](inputs[("color", 0, 0)].to(DEVICE)))
            pred_disp, _ = disparity2depth(outputs[("disp", 0)], MIN_DEPTH, MAX_DEPTH)
            pred_disp    = pred_disp.cpu()[:, 0].numpy()
            
            result_list.append(outputs)
            disparity_list.append(pred_disp)
    disparity_list = np.concatenate(disparity_list)


    errors_list = []
    for index in tqdm(range(len(disparity_list))):
        pred_disparity = disparity_list[index]
        ground_truth   = ground_truth_list[index]
        height, width  = ground_truth.shape

        pred_disparity = cv2.resize(pred_disparity, (width, height))
        pred_depth     = 1 / pred_disparity

        if (args.splits == "eigen_zhou") or (args.splits == "eigen_full"):
            mask      = np.logical_and(ground_truth > MIN_DEPTH, ground_truth < MAX_DEPTH)
            crop      = np.array([153, 371, 44, 1197]).astype(np.int32)
            crop_mask = np.zeros(mask.shape)
            crop_mask[crop[0]:crop[1], crop[2]:crop[3]] = 1
            mask = np.logical_and(mask, crop_mask)
        else:
            mask = ground_truth > 0.
        
        pred_depth   = pred_depth[mask]
        pred_depth   *= 1
        ground_truth = ground_truth[mask]
        pred_depth   *= np.median(ground_truth) / np.median(pred_depth)

        pred_depth[pred_depth < MIN_DEPTH] = MIN_DEPTH
        pred_depth[pred_depth > MAX_DEPTH] = MAX_DEPTH
        errors_list.append(compute_depth_error(ground_truth, pred_depth, "numpy"))
    mean_errors = np.array(errors_list).mean(0)

    print(">>>   abs_rel   sqrt_rel  rmse      rmse_log  a1        a2        a3")
    print(">>>" + ("   {:4.3f}  " * 7).format(*mean_errors.tolist()))
    return result_list



if __name__ == "__main__":
    def options(name):
        parser = argparse.ArgumentParser(description = "Input optional guidance for training")
        parser.add_argument("--datapath",
            default = "./dataset/kitti",
            type = str, help = "훈련 폴더가 있는 곳")
        parser.add_argument("--splits",
            default = "eigen_zhou",
            type = str, help = ["eigen_zhou", "eigen_full"])

        parser.add_argument("--encoder_path",
            default = weight[name]["encoder"],
            type = str, help = "인코더 가중치 경로")
        parser.add_argument("--decoder_path",
            default = weight[name]["decoder"],
            type = str, help = "디코더 가중치 경로")
        args = parser.parse_args()
        return args
    
    epo = 21
    weight = {
        "mono_640x192": {
            "encoder": "./model_save/mono_640x192/encoder.pth",
            "decoder": "./model_save/mono_640x192/depth.pth",},
        "ms_640x192": {
            "encoder": "./model_save/ms_640x192/encoder.pth",
            "decoder": "./model_save/ms_640x192/depth.pth",},
        "mono": {
            "encoder": "./model_save/mono/encoder{}.pt".format(epo),
            "decoder": "./model_save/mono/decoder{}.pt".format(epo)}}


    for name in ["mono_640x192", "ms_640x192", "mono"]:
        print("")
        print("Type {}".format(name))
        print("")
        return_result = inference(options(name))
