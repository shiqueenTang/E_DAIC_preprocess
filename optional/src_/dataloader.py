import os
os.environ['TORCHAUDIO_USE_SOX'] = '0'

import csv
import json
import torchaudio
import numpy as np
import torch
import torch.nn.functional
from torch.utils.data import Dataset
import random
# import librosa 

# def make_index_dict(label_csv):
#     index_lookup = {}
#     with open(label_csv, 'r') as f:
#         csv_reader = csv.DictReader(f)
#         line_count = 0
#         for row in csv_reader:
#             index_lookup[row['mid']] = row['index']
#             line_count += 1
    # return index_lookup

# def make_name_dict(label_csv):
#     name_lookup = {}
#     with open(label_csv, 'r') as f:
#         csv_reader = csv.DictReader(f)
#         line_count = 0
#         for row in csv_reader:
#             name_lookup[row['index']] = row['display_name']
#             line_count += 1
#     return name_lookup

# def lookup_list(index_list, label_csv):
#     label_list = []
#     table = make_name_dict(label_csv)
#     for item in index_list:
#         label_list.append(table[item])
#     return label_list

def preemphasis(signal,coeff=0.97):
    """perform preemphasis on the input signal.

    :param signal: The signal to filter.
    :param coeff: The preemphasis coefficient. 0 is none, default 0.97.
    :returns: the filtered signal.
    """
    return np.append(signal[0],signal[1:]-coeff*signal[:-1])

class AudiosetDataset(Dataset):
    def __init__(self, dataset_json_file=None, audio_conf=None, label_csv=None, rev_audio=False):
        """
        Dataset that manages audio recordings
        :param audio_conf: Dictionary containing the audio loading and preprocessing settings
        :param dataset_json_file
        """
        self.fshift = audio_conf.get('fshift')
        self.rev_audio = rev_audio
        if self.fshift is None:
            self.fshift = 10
        self.datapath = dataset_json_file
        with open(dataset_json_file, 'r') as fp:
            data_json = json.load(fp)

        self.data = data_json['data']
        self.audio_conf = audio_conf
        print('---------------the {:s} dataloader---------------'.format(self.audio_conf.get('mode')))
        self.melbins = self.audio_conf.get('num_mel_bins')
        self.freqm = self.audio_conf.get('freqm')
        self.timem = self.audio_conf.get('timem')
        print('now using following mask: {:d} freq, {:d} time'.format(self.audio_conf.get('freqm'), self.audio_conf.get('timem')))
        self.mixup = self.audio_conf.get('mixup')
        print('now using mix-up with rate {:f}'.format(self.mixup))
        self.dataset = self.audio_conf.get('dataset')
        print('now process ' + self.dataset)
        # dataset spectrogram mean and std, used to normalize the input
        self.norm_mean = self.audio_conf.get('mean')
        self.norm_std = self.audio_conf.get('std')
        # skip_norm is a flag that if you want to skip normalization to compute the normalization stats using src/get_norm_stats.py, if Ture, input normalization will be skipped for correctly calculating the stats.
        # set it as True ONLY when you are getting the normalization stats.
        self.skip_norm = self.audio_conf.get('skip_norm') if self.audio_conf.get('skip_norm') else False
        if self.skip_norm:
            print('now skip normalization (use it ONLY when you are computing the normalization stats).')
        else:
            print('use dataset mean {:.3f} and std {:.3f} to normalize the input.'.format(self.norm_mean, self.norm_std))
        # if add noise for data augmentation
        self.noise = self.audio_conf.get('noise')
        if self.noise == True:
            print('now use noise augmentation')

        # self.index_dict = make_index_dict(label_csv)
        # self.label_num = len(self.index_dict)
        # print('number of classes is {:d}'.format(self.label_num))

    def _wav2fbank(self, filename, filename2=None):
        # mixup
        if filename2 == None:
            try:
                waveform, sr = torchaudio.load(filename)
                # print(f'original wave shape:{waveform.shape}')
            except:
                return None, None
            waveform = waveform - waveform.mean()
        # mixup
        else:
            try:
                waveform1, sr = torchaudio.load(filename)
                waveform2, _ = torchaudio.load(filename2)
            except:
                return None, None

            waveform1 = waveform1 - waveform1.mean()
            waveform2 = waveform2 - waveform2.mean()

            if waveform1.shape[1] != waveform2.shape[1]:
                if waveform1.shape[1] > waveform2.shape[1]:
                    # padding
                    temp_wav = torch.zeros(waveform1.shape)
                    temp_wav[:, 0:waveform2.shape[1]] = waveform2
                    waveform2 = temp_wav
                else:
                    # cutting
                    waveform2 = waveform2[:, 0:waveform1.shape[1]]

            # sample lambda from uniform distribution
            #mix_lambda = random.random()
            # sample lambda from beta distribtion
            mix_lambda = np.random.beta(10, 10)

            mix_waveform = mix_lambda * waveform1 + (1 - mix_lambda) * waveform2
            waveform = mix_waveform - mix_waveform.mean()

        if self.rev_audio:
            waveform = waveform.flip(1) # Check!

        fbank = torchaudio.compliance.kaldi.fbank(waveform, htk_compat=True, sample_frequency=sr, use_energy=False,
                                                  window_type='hanning', num_mel_bins=self.melbins, dither=0.0, frame_shift=self.fshift)

        target_length = self.audio_conf.get('target_length')
        n_frames = fbank.shape[0]

        p = target_length - n_frames

        # cut and pad
        if p > 0:
            m = torch.nn.ZeroPad2d((0, 0, 0, p))
            fbank = m(fbank)
        elif p < 0:
            fbank = fbank[0:target_length, :]

        freqm = torchaudio.transforms.FrequencyMasking(self.freqm)
        timem = torchaudio.transforms.TimeMasking(self.timem)

        fbank = torch.transpose(fbank, 0, 1)
        # this is just to satisfy new torchaudio version, which only accept [1, freq, time]
        fbank = fbank.unsqueeze(0)
        if self.freqm != 0:
            fbank = freqm(fbank)
        if self.timem != 0:
            fbank = timem(fbank)
        # squeeze it back, it is just a trick to satisfy new torchaudio version
        fbank = fbank.squeeze(0)
        fbank = torch.transpose(fbank, 0, 1)
        fbank = (fbank - self.norm_mean) / (self.norm_std * 2)

        return fbank

    def __getitem__(self, index):
        """
        returns: image, audio, nframes
        where image is a FloatTensor of size (3, H, W)
        audio is a FloatTensor of size (N_freq, N_frames) for spectrogram, or (N_frames) for waveform
        nframes is an integer
        """
       
        fbank = None
        while True:
            datum = self.data[index]
            file_name = datum['wav'].split('/')[-1]
            fbank, _ = self._wav2fbank(datum['wav'])
            # print(f'processed data shape {fbank.shape}')
            
            if fbank is not None:
                break
                
            print('dataloading failed, retrying...')
            index = random.randint(0, len(self.data)-1)

        freqm = torchaudio.transforms.FrequencyMasking(self.freqm)
        timem = torchaudio.transforms.TimeMasking(self.timem)
        fbank = torch.transpose(fbank, 0, 1)
        # this is just to satisfy new torchaudio version, which only accept [1, freq, time]
        fbank = fbank.unsqueeze(0)
        if self.freqm != 0:
            fbank = freqm(fbank)
        if self.timem != 0:
            fbank = timem(fbank)
        # squeeze it back, it is just a trick to satisfy new torchaudio version
        fbank = fbank.squeeze(0)
        fbank = torch.transpose(fbank, 0, 1)
        fbank = (fbank - self.norm_mean) / (self.norm_std * 2)

        return fbank, file_name # audio_input, label, path

    def __len__(self):
        return len(self.data)