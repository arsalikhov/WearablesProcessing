# ------------------------------------------ NEED TO EDIT ECGDETECTORS PACKAGE ----------------------------------------
# Change final line in swt_detector() function to "return filt_peaks, swt_ecg, filtered_squared"

import ImportEDF
import Filtering

from ecgdetectors import Detectors
# https://github.com/luishowell/ecg-detectors

from matplotlib import pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from datetime import datetime
from random import randint
from matplotlib.ticker import PercentFormatter
import scipy.stats as stats
import pywt

# --------------------------------------------------------------------------------------------------------------------
# -------------------------------------------------------- Data Import -----------------------------------------------
# --------------------------------------------------------------------------------------------------------------------


class ECG:

    def __init__(self, filepath=None, run_qc_check=True,
                 start_offset=0, end_offset=0,
                 epoch_len=15, load_accel=False,
                 filter_data=False, low_f=1, high_f=30, f_type="bandpass"):
        """Class that contains raw and processed ECG data.

        :argument
        DATA IMPORT
        -filepath: full pathway to EDF file
        -start_offset, end_offset: indexes used to crop data to match other devices

        FILTERING: used for visualization (not peak detection)
        -filter: whether or not to filter the data
        -low_f, high_1: cut-off frequencies for the filter. Set to None if irrelevant. In Hz.
        -f_type: type of filter; "lowpass", "highpass", "bandpass"
        """

        print()
        print("============================================= ECG DATA ==============================================")

        self.filepath = filepath
        self.filename = filepath.split("/")[-1]
        self.subject_id = self.filename.split("_")[2]
        self.epoch_len = epoch_len
        self.start_offset = start_offset
        self.end_offset = end_offset

        self.load_accel = load_accel

        self.filter_data = filter_data
        self.low_f = low_f
        self.high_f = high_f
        self.f_type = f_type

        self.accel_sample_rate = 1
        self.accel_x = None
        self.accel_y = None
        self.accel_z = None
        self.accel_vm = None
        self.svm = []

        # Raw data
        self.ecg = ImportEDF.Bittium(filepath=self.filepath, load_accel=self.load_accel,
                                     start_offset=self.start_offset, end_offset=self.end_offset,
                                     low_f=self.low_f, high_f=self.high_f, f_type=self.f_type)

        self.sample_rate = self.ecg.sample_rate
        self.accel_sample_rate = self.ecg.accel_sample_rate
        self.raw = self.ecg.raw
        self.filtered = self.ecg.filtered
        self.timestamps = self.ecg.timestamps
        self.epoch_timestamps = self.ecg.epoch_timestamps

        self.accel_x, self.accel_y, self.accel_z, self.accel_vm = self.ecg.x, self.ecg.y, self.ecg.z, self.ecg.vm

        del self.ecg

        self.wavelet = self.wavelet_transform()[:len(self.timestamps)]

        # Performs quality control check on raw data and epochs data
        if run_qc_check:
            self.epoch_validity, self.epoch_hr, self.avg_voltage, self.rr_sd, self.r_peaks = self.check_quality()

            # List of epoched heart rates but any invalid epoch is marked as None instead of 0 (as is self.epoch_hr)
            self.valid_hr = [self.epoch_hr[i] if self.epoch_validity[i] == 0 else None for i in range(len(self.epoch_hr))]

            self.quality_report = self.generate_quality_report()

        self.rolling_avg_hr = None

    def wavelet_transform(self):

        unfiltered_ecg = self.raw

        swt_level = 3
        padding = -1
        for i in range(1000):
            if (len(unfiltered_ecg) + i) % 2 ** swt_level == 0:
                padding = i
                break

        if padding > 0:
            unfiltered_ecg = np.pad(unfiltered_ecg, (0, padding), 'edge')
        elif padding == -1:
            print("Padding greater than 1000 required\n")

        swt_ecg = pywt.swt(unfiltered_ecg, 'db3', level=swt_level)
        swt_ecg = np.array(swt_ecg)
        swt_ecg = swt_ecg[0, 1, :]

        return swt_ecg

    def epoch_accel(self):

        for i in range(0, len(self.accel_vm), int(self.accel_sample_rate * self.epoch_len)):

            if i + self.epoch_len * self.accel_sample_rate > len(self.accel_vm):
                break

            vm_sum = sum(self.accel_vm[i:i + self.epoch_len * self.accel_sample_rate])

            self.svm.append(round(vm_sum, 5))

    def check_quality(self):
        """Performs quality check using Orphanidou et al. (2015) algorithm that has been tweaked to factor in voltage
           range as well.

           This function runs a loop that creates object from the class CheckQuality for each epoch in the raw data.
        """

        print("\n" + "Running quality check with Orphanidou et al. (2015) algorithm...")
        print("This can take a few minutes. Go grab a coffee or something.")

        t0 = datetime.now()

        validity_list = []
        epoch_hr = []
        avg_voltage = []
        rr_sd = []
        r_peaks = []

        # for start_index in range(0, int(len(self.raw)), self.epoch_len*self.sample_rate):
        for start_index in range(0, int(len(self.raw)), self.epoch_len * self.sample_rate):

            qc = CheckQuality(ecg_object=self, start_index=start_index, epoch_len=self.epoch_len)

            avg_voltage.append(qc.volt_range)

            if qc.valid_period:
                validity_list.append(0)
                epoch_hr.append(round(qc.hr, 2))
                rr_sd.append(qc.rr_sd)

                for peak in qc.r_peaks_index_all:
                    r_peaks.append(peak)
                for peak in qc.removed_peak:
                    r_peaks.append(peak + start_index)

                r_peaks = sorted(r_peaks)

            if not qc.valid_period:
                validity_list.append(1)
                epoch_hr.append(0)
                rr_sd.append(0)

        t1 = datetime.now()
        proc_time = (t1 - t0).seconds
        print("\n" + "Quality check complete ({} seconds).".format(round(proc_time, 2)))
        print("-Processing time of {} seconds per "
              "hour of data.".format(round(proc_time / (len(self.raw)/self.sample_rate/3600), 1)))

        return validity_list, epoch_hr, avg_voltage, rr_sd, r_peaks

    def generate_quality_report(self):
        """Calculates how much of the data was usable. Returns values in dictionary."""

        invalid_epochs = self.epoch_validity.count(1)  # number of invalid epochs
        hours_lost = round(invalid_epochs / (60 / self.epoch_len) / 60, 2)  # hours of invalid data
        perc_invalid = round(invalid_epochs / len(self.epoch_validity) * 100, 1)  # percent of invalid data

        # Longest valid period
        longest_valid = count = 0
        current = ''
        for epoch in self.epoch_validity:
            if epoch == current and epoch == 0:
                count += 1
            else:
                count = 1
                current = epoch
            longest_valid = max(count, longest_valid)

        # Longest invalid
        longest_invalid = count = 0
        current = ''
        for epoch in self.epoch_validity:
            if epoch == current and epoch == 1:
                count += 1
            else:
                count = 1
                current = epoch
            longest_invalid = max(count, longest_invalid)

        quality_report = {"Invalid epochs": invalid_epochs, "Hours lost": hours_lost,
                          "Percent invalid": perc_invalid,
                          "Longest valid period": longest_valid, "Longest invalid period": longest_invalid,
                          "Average valid duration (minutes)": None}

        print("-{}% of the data is valid.".format(round(100 - perc_invalid), 3))

        return quality_report

    def plot_histogram(self):
        """Generates a histogram of heart rates over the course of the collection with a bin width of 5 bpm.
           Marks calculated average and resting HR."""

        # Data subset: only valid HRs
        valid_heartrates = [i for i in self.valid_hr if i is not None]
        avg_hr = sum(valid_heartrates) / len(valid_heartrates)

        # Bins of width 5bpm between 40 and 180 bpm
        n_bins = np.arange(40, 180, 5)

        plt.figure(figsize=(10, 6))
        plt.hist(x=valid_heartrates, weights=np.ones(len(valid_heartrates)) / len(valid_heartrates), bins=n_bins,
                 edgecolor='black', color='grey')
        plt.axvline(x=avg_hr, color='red', linestyle='dashed', label="Average HR ({} bpm)".format(round(avg_hr, 1)))

        plt.gca().yaxis.set_major_formatter(PercentFormatter(1))

        plt.ylabel("% of Epochs")
        plt.xlabel("HR (bpm)")
        plt.title("Heart Rate Histogram")
        plt.legend(loc='upper left')
        plt.show()

    def plot_qc_segment(self, input_index=None, template_data='filtered', plot_steps=True, plot_template=False):
        """Method that generates a random 10-minute sample of data. Overlays filtered data with quality check output.

        :argument
        -start_index: able to input desired start index. If None, randomly generated
        """

        # Generates random start index
        if input_index is not None:
            start_index = input_index
        if input_index is None:
            start_index = randint(0, len(self.filtered) - self.epoch_len * self.sample_rate)

        # Rounds random start to an index that corresponds to start of an epoch
        start_index -= start_index % (self.epoch_len * self.sample_rate)

        print("\n" + "Index {}.".format(start_index))

        # End index: one epoch
        end_index = start_index + self.epoch_len * self.sample_rate

        # Data point index converted to seconds
        seconds_seq_raw = np.arange(0, self.epoch_len * self.sample_rate) / self.sample_rate

        # Epoch's quality check
        validity_data = CheckQuality(ecg_object=self, start_index=start_index,
                                     epoch_len=self.epoch_len, template_data=template_data)

        print()
        print("Valid HR: {} (passed {}/5 conditions)".format(validity_data.rule_check_dict["Valid Period"],
                                                             validity_data.rule_check_dict["HR Valid"] +
                                                             validity_data.rule_check_dict["Max RR Interval Valid"] +
                                                             validity_data.rule_check_dict["RR Ratio Valid"] +
                                                             validity_data.rule_check_dict["Voltage Range Valid"] +
                                                             validity_data.rule_check_dict["Correlation Valid"]))

        print("-HR range ({} bpm): {}".format(validity_data.rule_check_dict["HR"],
                                              validity_data.rule_check_dict["HR Valid"]))
        print("-Max RR interval ({} sec): {}".format(validity_data.rule_check_dict["Max RR Interval"],
                                                     validity_data.rule_check_dict["Max RR Interval Valid"]))
        print("-RR ratio ({}): {}".format(validity_data.rule_check_dict["RR Ratio"],
                                          validity_data.rule_check_dict["RR Ratio Valid"]))
        print("-Voltage range ({} uV): {}".format(validity_data.rule_check_dict["Voltage Range"],
                                                  validity_data.rule_check_dict["Voltage Range Valid"]))
        print("-Correlation (r={}): {}".format(validity_data.rule_check_dict["Correlation"],
                                               validity_data.rule_check_dict["Correlation Valid"]))

        # Plot

        if plot_template:
            plt.close("all")

            fig, (ax1, ax2, ax3) = plt.subplots(3, figsize=(10, 7))

            valid_period = "Valid" if validity_data.rule_check_dict["Valid Period"] else "Invalid"

            ax1.set_title("Participant {}: {} (index = {})".format(self.subject_id, valid_period, start_index))

            # Filtered ECG data
            ax1.plot(seconds_seq_raw, self.raw[start_index:end_index], color='black', label="Raw ECG")
            ax1.set_ylabel("Voltage")
            ax1.legend(loc='upper left')

            # Wavelet data
            ax2.plot(np.arange(0, len(validity_data.wavelet)) / self.sample_rate, validity_data.wavelet,
                     color='green', label="Wavelet")
            ax2.plot(validity_data.r_peaks / self.sample_rate,
                     [validity_data.wavelet[peak] for peak in validity_data.r_peaks],
                     linestyle="", marker="x", color='black')
            ax2.set_ylabel("Voltage")
            ax2.legend()

            for peak in validity_data.removed_peak:
                ax2.plot(np.arange(0, len(validity_data.wavelet))[peak] / self.sample_rate,
                         validity_data.wavelet[peak], marker="x", color='red')

            for i, window in enumerate(validity_data.ecg_windowed):
                ax3.plot(np.arange(0, len(window)) / self.sample_rate, window, color='black')

            ax3.plot(np.arange(0, len(validity_data.average_qrs)) / self.sample_rate, validity_data.average_qrs,
                     label="QRS template ({} data; r={})".format(template_data, validity_data.average_r),
                     color='red', linestyle='dashed')

            ax3.legend()
            ax3.set_ylabel("Voltage")
            ax3.set_xlabel("Seconds")

        if plot_steps:
            validity_data.plot_steps(start_index=start_index)

        return validity_data

    def plot_all_data(self, downsample_ratio=2):

        xfmt = mdates.DateFormatter("%Y-%m-%d \n%H:%M:%S")
        locator = mdates.HourLocator(byhour=[0, 6, 12, 18], interval=1)

        fig, (ax1, ax2) = plt.subplots(2, sharex='col', figsize=(10, 6))
        plt.subplots_adjust(bottom=.17)

        plt.suptitle("ECG Data ({} Hz)".format(round(self.sample_rate/downsample_ratio, 1)))

        ax1.plot(self.timestamps[::downsample_ratio], self.raw[::downsample_ratio],
                 color='red', label="Raw")
        ax1.set_ylabel("Voltage")
        ax1.legend(loc='upper right')

        fs_ratio = int(self.sample_rate / self.accel_sample_rate)

        ax2.plot(self.timestamps[::fs_ratio], self.accel_x,
                 color='black', label="X")
        ax2.plot(self.timestamps[::fs_ratio], self.accel_y,
                 color='dodgerblue', label="Y")
        ax2.legend(loc='upper left')
        ax2.set_ylabel("mG")

        ax2.xaxis.set_major_formatter(xfmt)
        ax2.xaxis.set_major_locator(locator)
        plt.xticks(rotation=45, fontsize=8)

    def plot_validity(self, downsample_ratio=2):

        fig, (ax1, ax2, ax3) = plt.subplots(3, sharex="col")

        plt.suptitle("ECG Validity ({}% valid)".format(100-self.quality_report["Percent invalid"]))

        ax1.plot(self.timestamps[::downsample_ratio], self.filtered[::downsample_ratio],
                 color='black', label="Filtered")
        ax1.set_ylabel("Voltage")
        ax1.legend()

        ax2.plot(self.epoch_timestamps, self.valid_hr, color='red', label="HR (bpm)")
        ax2.set_ylabel("BPM")

        # ax3.plot(self.epoch_timestamps, self.epoch_validity, color='green')
        ax3.set_ylabel("1 = invalid")
        ax3.fill_between(x=self.epoch_timestamps, y1=0, y2=self.epoch_validity, color='grey')

        xfmt = mdates.DateFormatter("%a, %I:%M %p")
        locator = mdates.HourLocator(byhour=[0, 12], interval=1)

        ax3.xaxis.set_major_formatter(xfmt)
        ax3.xaxis.set_major_locator(locator)
        plt.xticks(rotation=45, fontsize=8)

# --------------------------------------------------------------------------------------------------------------------
# ------------------------------------------------------- Quality Check ----------------------------------------------
# --------------------------------------------------------------------------------------------------------------------


class CheckQuality:
    """Class method that implements the Orphanidou ECG signal quality assessment algorithm on raw ECG data.

       Orphanidou, C. et al. (2015). Signal-Quality Indices for the Electrocardiogram and Photoplethysmogram:
       Derivation and Applications to Wireless Monitoring. IEEE Journal of Biomedical and Health Informatics.
       19(3). 832-838.
    """

    def __init__(self, ecg_object, start_index, template_data='filtered', voltage_thresh=250, epoch_len=15):
        """Initialization method.

        :param
        -ecg_object: EcgData class instance created by ImportEDF script
        -random_data: runs algorithm on randomly-generated section of data; False by default.
                      Takes priority over start_index.
        -start_index: index for windowing data; 0 by default
        -epoch_len: window length in seconds over which algorithm is run; 15 seconds by default
        """

        self.voltage_thresh = voltage_thresh
        self.epoch_len = epoch_len
        self.fs = ecg_object.sample_rate
        self.start_index = start_index
        self.template_data = template_data

        self.ecg_object = ecg_object

        self.raw_data = ecg_object.raw[self.start_index:self.start_index+self.epoch_len*self.fs]
        self.filt_data = ecg_object.filtered[self.start_index:self.start_index+self.epoch_len*self.fs]
        self.wavelet = None
        self.filt_squared = None

        self.index_list = np.arange(0, len(self.raw_data), self.epoch_len*self.fs)

        self.rule_check_dict = {"Valid Period": False,
                                "HR Valid": False, "HR": None,
                                "Max RR Interval Valid": False, "Max RR Interval": None,
                                "RR Ratio Valid": False, "RR Ratio": None,
                                "Voltage Range Valid": False, "Voltage Range": None,
                                "Correlation Valid": False, "Correlation": None,
                                "Accel Counts": None}

        # prep_data parameters
        self.r_peaks = None
        self.r_peaks_index_all = None
        self.rr_sd = None
        self.removed_peak = []
        self.enough_beats = True
        self.hr = 0
        self.delta_rr = []
        self.removal_indexes = []
        self.rr_ratio = None
        self.volt_range = 0

        # apply_rules parameters
        self.valid_hr = None
        self.valid_rr = None
        self.valid_ratio = None
        self.valid_range = None
        self.valid_corr = None
        self.rules_passed = None

        # adaptive_filter parameters
        self.median_rr = None
        self.ecg_windowed = []
        self.average_qrs = None
        self.average_r = 0

        # calculate_correlation parameters
        self.beat_ppmc = []
        self.valid_period = None

        """RUNS METHODS"""
        # Peak detection and basic outcome measures
        self.prep_data()

        # Runs rules check if enough peaks found
        if self.enough_beats:
            self.adaptive_filter(template_data=self.template_data)
            self.calculate_correlation()
            self.apply_rules()

        if self.valid_period:
            self.r_peaks_index_all = [peak + start_index for peak in self.r_peaks]

    def prep_data(self):
        """Function that:
        -Initializes ecgdetector class instance
        -Runs stationary wavelet transform peak detection
            -Implements 0.1-10Hz bandpass filter
            -DB3 wavelet transformation
            -Pan-Tompkins peak detection thresholding
        -Calculates RR intervals
        -Removes first peak if it is within median RR interval / 2 from start of window
        -Calculates average HR in the window
        -Determines if there are enough beats in the window to indicate a possible valid period
        """

        # Initializes Detectors class instance with sample rate
        detectors = Detectors(self.fs)

        # Runs peak detection on raw data ----------------------------------------------------------------------------
        # Uses ecgdetectors package -> stationary wavelet transformation + Pan-Tompkins peak detection algorithm
        self.r_peaks, self.wavelet, self.filt_squared = detectors.swt_detector(unfiltered_ecg=self.filt_data)

        # Checks to see if there are enough potential peaks to correspond to correct HR range ------------------------
        # Requires number of beats in window that corresponds to ~40 bpm to continue
        # Prevents the math in the self.hr calculation from returning "valid" numbers with too few beats
        # i.e. 3 beats in 3 seconds (HR = 60bpm) but nothing detected for rest of epoch
        if len(self.r_peaks) >= np.floor(40/60*self.epoch_len):
            self.enough_beats = True

            n_beats = len(self.r_peaks)  # number of beats in window
            delta_t = (self.r_peaks[-1] - self.r_peaks[0]) / self.fs  # time between first and last beat, seconds
            self.hr = 60 * (n_beats-1) / delta_t  # average HR, bpm

        # Stops function if not enough peaks found to be a potential valid period
        # Threshold corresponds to number of beats in the window for a HR of 40 bpm
        if len(self.r_peaks) < np.floor(40/60*self.epoch_len):
            self.enough_beats = False
            self.valid_period = False
            return

        # Calculates RR intervals in seconds -------------------------------------------------------------------------
        for peak1, peak2 in zip(self.r_peaks[:], self.r_peaks[1:]):
            rr_interval = (peak2 - peak1) / self.fs
            self.delta_rr.append(rr_interval)

        # Approach 1: median RR characteristics ----------------------------------------------------------------------
        # Calculates median RR-interval in seconds
        median_rr = np.median(self.delta_rr)

        # SD of RR intervals in ms
        self.rr_sd = np.std(self.delta_rr) * 1000

        # Converts median_rr to samples
        self.median_rr = int(median_rr * self.fs)

        # Removes any peak too close to start/end of data section: affects windowing later on ------------------------
        # Peak removed if within median_rr/2 samples of start of window
        # Peak removed if within median_rr/2 samples of end of window
        for i, peak in enumerate(self.r_peaks):
            # if peak < (self.median_rr/2 + 1) or (self.epoch_len*self.fs - peak) < (self.median_rr/2 + 1):
            if peak < (self.median_rr / 2 + 1) or (self.epoch_len * self.fs - peak) < (self.median_rr / 2 + 1):
                self.removed_peak.append(self.r_peaks.pop(i))
                self.removal_indexes.append(i)

        # Removes RR intervals corresponding to
        if len(self.removal_indexes) != 0:
            self.delta_rr = [self.delta_rr[i] for i in range(len(self.r_peaks)) if i not in self.removal_indexes]

        # Calculates range of ECG voltage ----------------------------------------------------------------------------
        self.volt_range = max(self.raw_data) - min(self.raw_data)

    def adaptive_filter(self, template_data="filtered"):
        """Method that runs an adaptive filter that generates the "average" QRS template for the window of data.

        - Calculates the median RR interval
        - Generates a sub-window around each peak, +/- RR interval/2 in width
        - Deletes the final beat sub-window if it is too close to end of data window
        - Calculates the "average" QRS template for the window
        """

        # Approach 1: calculates median RR-interval in seconds  -------------------------------------------------------
        # See previous method

        # Approach 2: takes a window around each detected R-peak of width peak +/- median_rr/2 ------------------------
        for peak in self.r_peaks:
            if template_data == "raw":
                window = self.raw_data[peak - int(self.median_rr / 2):peak + int(self.median_rr / 2)]
            if template_data == "filtered":
                window = self.filt_data[peak - int(self.median_rr / 2):peak + int(self.median_rr / 2)]
            if template_data == "wavelet":
                window = self.wavelet[peak - int(self.median_rr / 2):peak + int(self.median_rr / 2)]

            self.ecg_windowed.append(window)  # Adds window to list of windows

        # Approach 3: determine average QRS template ------------------------------------------------------------------
        self.ecg_windowed = np.asarray(self.ecg_windowed)[1:]  # Converts list to np.array; omits first empty array

        # Calculates "correct" length (samples) for each window (median_rr number of datapoints)
        correct_window_len = 2*int(self.median_rr/2)

        # Removes final beat's window if its peak is less than median_rr/2 samples from end of window
        # Fixes issues when calculating average_qrs waveform
        if len(self.ecg_windowed[-1]) != correct_window_len:
            self.removed_peak.append(self.r_peaks.pop(-1))
            self.ecg_windowed = self.ecg_windowed[:-2]

        # Calculates "average" heartbeat using windows around each peak
        try:
            self.average_qrs = np.mean(self.ecg_windowed, axis=0)
        except ValueError:
            print("Failed to calculate mean QRS template.")

    def calculate_correlation(self):
        """Method that runs a correlation analysis for each beat and the average QRS template.

        - Runs a Pearson correlation between each beat and the QRS template
        - Calculates the average individual beat Pearson correlation value
        - The period is deemed valid if the average correlation is >= 0.66, invalid is < 0.66
        """

        # Calculates correlation between each beat window and the average beat window --------------------------------
        for beat in self.ecg_windowed:
            r = stats.pearsonr(x=beat, y=self.average_qrs)
            self.beat_ppmc.append(abs(r[0]))

        self.average_r = float(np.mean(self.beat_ppmc))
        self.average_r = round(self.average_r, 3)

    def apply_rules(self):
        """First stage of algorithm. Checks data against three rules to determine if the window is potentially valid.
        -Rule 1: HR needs to be between 40 and 180bpm
        -Rule 2: no RR interval can be more than 3 seconds
        -Rule 3: the ratio of the longest to shortest RR interval is less than 2.2
        -Rule 4: the amplitude range of the raw ECG voltage must exceed n microV (approximate range for non-wear)
        -Rule 5: the average correlation coefficient between each beat and the "average" beat must exceed 0.66
        -Verdict: all rules need to be passed
        """

        # Rule 1: "The HR extrapolated from the sample must be between 40 and 180 bpm" -------------------------------
        if 40 <= self.hr <= 180:
            self.valid_hr = True
        else:
            self.valid_hr = False

        # Rule 2: "the maximum acceptable gap between successive R-peaks is 3s ---------------------------------------
        for rr_interval in self.delta_rr:
            if rr_interval < 3:
                self.valid_rr = True

            if rr_interval >= 3:
                self.valid_rr = False
                break

        # Rule 3: "the ratio of the maximum beat-to-beat interval to the minimum beat-to-beat interval... ------------
        # should be less than 2.5"
        self.rr_ratio = max(self.delta_rr) / min(self.delta_rr)

        if self.rr_ratio >= 2.5:
            self.valid_ratio = False

        if self.rr_ratio < 2.5:
            self.valid_ratio = True

        # Rule 4: the range of the raw ECG signal needs to be >= 250 microV ------------------------------------------
        if self.volt_range <= self.voltage_thresh:
            self.valid_range = False

        if self.volt_range > self.voltage_thresh:
            self.valid_range = True

        # Rule 5: Determines if average R value is above threshold of 0.66 -------------------------------------------
        if self.average_r >= 0.66:
            self.valid_corr = True

        if self.average_r < 0.66:
            self.valid_corr = False

        # FINAL VERDICT: valid period if all rules are passed --------------------------------------------------------
        if self.valid_hr and self.valid_rr and self.valid_ratio and self.valid_range and self.valid_corr:
            self.valid_period = True
        else:
            self.valid_period = False

        self.rule_check_dict = {"Valid Period": self.valid_period,
                                "HR Valid": self.valid_hr, "HR": round(self.hr, 1),
                                "Max RR Interval Valid": self.valid_rr, "Max RR Interval": round(max(self.delta_rr), 1),
                                "RR Ratio Valid": self.valid_ratio, "RR Ratio": round(self.rr_ratio, 1),
                                "Voltage Range Valid": self.valid_range, "Voltage Range": round(self.volt_range, 1),
                                "Correlation Valid": self.valid_corr, "Correlation": self.average_r,
                                "Accel Flatline": None}

        if self.ecg_object.load_accel:
            accel_start = int(self.start_index / (self.ecg_object.sample_rate / self.ecg_object.accel_sample_rate))
            accel_end = accel_start + self.ecg_object.accel_sample_rate * self.epoch_len

            svm = sum(self.ecg_object.accel_vm[accel_start:accel_end])
            self.rule_check_dict["Accel Counts"] = round(svm, 2)

            flatline = True if max(self.ecg_object.accel_vm[accel_start:accel_end]) - \
                               min(self.ecg_object.accel_vm[accel_start:accel_end]) <= .05 else False
            self.rule_check_dict["Accel Flatline"] = flatline

            sd = np.std(self.ecg_object.accel_vm[accel_start:accel_end])
            self.rule_check_dict["Accel SD"] = sd

    def plot_steps(self, start_index=None):

        fig, (ax1, ax2, ax3, ax4) = plt.subplots(4, sharex="col", figsize=(10, 6))
        plt.suptitle("ECG Quality Check Processing Steps "
                     "(Index = {}; {} period)".format(start_index, "Valid" if self.valid_period else "Invalid"))

        # Raw ECG
        ax1.plot(np.arange(len(self.raw_data))/self.ecg_object.sample_rate, self.raw_data,
                 color='red', label="Raw")
        ax1.set_ylabel("Voltage")
        ax1.set_xlim(-.5, self.epoch_len * 1.25)
        ax1.legend()

        # Filtered ECG
        ax2.plot(np.arange(len(self.filt_data))/self.ecg_object.sample_rate, self.filt_data,
                 color='blue', label="Filtered")
        ax2.set_ylabel("Voltage")
        ax2.legend()

        # Wavelet ECG
        ax3.plot(np.arange(len(self.wavelet)) / self.ecg_object.sample_rate, self.wavelet,
                 color='green', label="Wavelet")
        ax3.set_ylabel("Voltage")
        ax3.legend()

        # Wavelet squared + filtered
        ax4.plot(np.arange(len(self.filt_squared))/self.ecg_object.sample_rate, self.filt_squared,
                 color='dodgerblue', label="Squared")
        ax4.plot([np.arange(len(self.filt_squared))[i]/self.ecg_object.sample_rate for i in self.r_peaks],
                 [self.filt_squared[i] for i in self.r_peaks], linestyle="", marker="x", color='black')
        ax4.fill_between(x=[0, self.median_rr / 2 / self.ecg_object.sample_rate],
                         y1=min(self.filt_squared), y2=max(self.filt_squared), color='grey', alpha=.5)
        ax4.fill_between(x=[self.epoch_len - self.median_rr / 2 / self.ecg_object.sample_rate, self.epoch_len],
                         y1=min(self.filt_squared), y2=max(self.filt_squared), color='grey', alpha=.5,
                         label="Ignored zone")
        ax4.set_ylabel("Voltage")
        ax4.set_xlabel("Time (s)")
        ax4.legend()


# --------------------------------------------------------------------------------------------------------------------
# ------------------------------------------------------- Running Code -----------------------------------------------
# --------------------------------------------------------------------------------------------------------------------

# Creates object and does all the processing
ecg = ECG(filepath="/Users/kyleweber/Desktop/Data/OND07/EDF/OND07_WTL_3025_01_BF.EDF",
          run_qc_check=False,
          start_offset=0, end_offset=0, epoch_len=15, load_accel=True,
          filter_data=False, low_f=1, high_f=30, f_type="bandpass")

"""Additional stuff. Highlight + right-click + "execute selection in python console" to run.

# Plots section of data and its stages of processing. Specify data index with "input_index" argument.
# Plots random segment if "input_index=None". Plot title states whether valid or invalid signal.
ecg.plot_qc_segment(input_index=None, template_data='filtered', plot_steps=True, plot_template=False)

# Plots section of raw and wavelet data with the 'average' QRS template.
# Plots random segment if "input_index=None". Plot title states whether valid or invalid signal.
ecg.plot_qc_segment(input_index=None, template_data='filtered', plot_steps=False, plot_template=True)

# Plots histogram of epoched HR distribution (5bpm bin width). Marks average HR. 
ecg.plot_histogram()

# Plots raw, filtered, and wavelet data. Able to set downsample ratio (defaults to 2).
ecg.plot_all_data(downsample_ratio=2)

# Viewing filtered data with quality check output
ecg.plot_validity(downsample_ratio=3)
"""
