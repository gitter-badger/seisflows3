#!/usr/bin/env python3
"""
The Pyatoa preprocessing module abstracts all preprocessing functionality
onto Pyatoa (https://github.com/bch0w/pyatoa/). The module defined below is
meant to set up and execute Pyatoa within a running SeisFlows3 workflow.
"""
import os
import sys
import logging
import numpy as np

import pyatoa

from glob import glob
from pyatoa.utils.images import merge_pdfs

from seisflows3.tools import unix, msg
from seisflows3.config import custom_import
from seisflows3.config import SeisFlowsPathsParameters

PAR = sys.modules["seisflows_parameters"]
PATH = sys.modules["seisflows_paths"]


class Pyatoa(custom_import("preprocess", "base")):
    """
    Data preprocessing class using the Pyaflowa class within the Pyatoa package.
    In charge of data discovery, preprocessing, filtering, misfiti
    quantification and data storage. The User does not need to implement Pyatoa,
    but rather interacts with it via the parameters and paths of SeisFlows3.
    """
    logger = logging.getLogger(__name__).getChild(__qualname__)

    def __init__(self):
        """
        These parameters should not be set by __init__!
        Attributes are just initialized as NoneTypes for clarity and docstrings

        :type data: str
        :param data: directory where data from the preprocessing is stored
        :type figures: str
        :param figures: directory where figures are stored
        :param logger: Class-specific logging module, log statements pushed
            from this logger will be tagged by its specific module/classname
        """
        self.pyaflowa = None
        self.path_datasets = None
        self.path_figures = None

    @property
    def required(self):
        """
        A hard definition of paths and parameters required by this class,
        alongside their necessity for the class and their string explanations.
        """
        sf = SeisFlowsPathsParameters()

        # Define the Parameters required by this module
        sf.par("UNIT_OUTPUT", required=True, par_type=str,
               docstr="Data units. Must match the synthetic output of external "
                      "solver. Available: ['DISP': displacement, "
                      "'VEL': velocity, 'ACC': acceleration]")

        sf.par("MIN_PERIOD", required=True, par_type=float,
               docstr="Minimum filter corner in seconds")

        sf.par("MAX_PERIOD", required=True, par_type=float,
               docstr="Maximum filter corner in seconds")

        sf.par("CORNERS", required=False, default=4, par_type=int,
               docstr="Number of filter corners")

        sf.par("CLIENT", required=False, par_type=str,
               docstr="Client name for ObsPy FDSN data gathering")

        sf.par("START_PAD", required=False, default=0, par_type=float,
               docstr="For data gathering; time before origin time to gather. "
                      "START_PAD >= T_0 in SPECFEM constants.h.in. "
                      "Positive values only")

        sf.par("END_PAD", required=True, par_type=float,
               docstr="For data gathering; time after origin time to gather. "
                      "END_PAD >= NT * DT (of Par_file). Positive values only")

        sf.par("ROTATE", required=False, default=False, par_type=bool,
               docstr="Rotate waveform components NEZ -> RTZ")

        sf.par("ADJ_SRC_TYPE", required=True, par_type=str,
               docstr="Adjoint source type to use. Available: "
                      "['cc': cross-correlation, 'mt': multitaper, "
                      "wav: waveform']")

        sf.par("PYFLEX_PRESET", required=True, par_type=str,
               docstr="Parameter map for Pyflex config. For available choices, "
                      "see Pyatoa docs page (pyatoa.rtfd.io)")

        sf.par("FIX_WINDOWS", required=False, default=False,
               par_type="bool or str",
               docstr="Time window evaluation: available: "
                      "[True, False, 'ITER', 'ONCE'] "
                      "True: Same windows for all but i01s00; "
                      "False: New windows at each evaluation; "
                      "'ITER': New windows at first evaluation of each iter; "
                      "'ONCE': New windows at first evaluation of workflow")

        sf.par("PLOT", required=False, default=True, par_type=bool,
               docstr="Plot waveforms and maps as .pdf files")

        sf.par("SNAPSHOT", required=False, default=True, par_type=bool,
               docstr="Copy ASDFDataSets on disk for data redundancy")

        sf.par("LOGGING", required=False, default="DEBUG", par_type=str,
               docstr="Log level. Available: "
                      "['null': no logging, 'warning': warnings only, "
                      "'info': task tracking, 'debug': log everything]")

        # Define the Paths required by this module
        sf.path("PREPROCESS", required=False,
                default=os.path.join(PATH.SCRATCH, "preprocess"),
                docstr="scratch path to store waveform data and figures")

        sf.path("DATA", required=False,
                docstr="Directory to locally stored data")

        return sf

    def check(self, validate=True):
        """ 
        Checks Parameter and Path files, will be run at the start of a Seisflows
        workflow to ensure that things are set appropriately.
        """
        if validate:
            self.required.validate()

        # Check that other modules have set parameters that will be used here
        for required_parameter in ["COMPONENTS", "FORMAT"]:
            assert(required_parameter in PAR), \
                f"Pyatoa requires {required_parameter}"

        assert(PAR.FORMAT.upper() == "ASCII"), \
            "Pyatoa preprocess requires PAR.FORMAT=='ASCII'"

        assert((PAR.DT * PAR.NT) >= (PAR.START_PAD + PAR.END_PAD)), \
            ("Pyatoa preprocess must have (PAR.START_PAD + PAR.END_PAD) >= "
             "(PAR.DT * PAR.NT), current values will not provide sufficiently"
             "long data traces")

    def setup(self):
        """
        Sets up data preprocessing machinery by establishing an internally
        defined directory structure that will be used to store the outputs 
        of the preprocessing workflow

        Akin to an __init__ class, but to be called externally by the workflow.
        """
        # Inititate a Pyaflowa object to make sure the machinery works
        self.pyaflowa = pyatoa.Pyaflowa(structure="seisflows", 
                                        sfpaths=PATH, sfpar=PAR, 
                                        source_prefix=PAR.SOURCE_PREFIX)

        # Pull path names from Pyaflowa to keep path structure in one place
        self.path_datasets = self.pyaflowa.path_structure.datasets
        self.path_figures = self.pyaflowa.path_structure.figures

    def prepare_eval_grad(self, cwd, source_name, taskid, **kwargs):
        """
        Prepare the gradient evaluation by gathering, preprocessing waveforms, 
        and measuring misfit between observations and synthetics using Pyatoa.

        Reads in observed and synthetic waveforms, applies optional
        preprocessing, assesses misfit, and writes out adjoint sources and
        STATIONS_ADJOINT file.

        .. note::
            Meant to be called by solver.eval_func(), may have unused arguments
            to keep functions general across subclasses.

        :type cwd: str
        :param cwd: current specfem working directory containing observed and
            synthetic seismic data to be read and processed. Should be defined
            by solver.cwd
        :type source_name: str
        :param source_name: the event id to be used for tagging and data lookup.
            Should be defined by solver.source_name
        :type taskid: int
        :param taskid: identifier of the currently running solver instance.
            Should be defined by solver.taskid
        :type filenames: list of str
        :param filenames: [not used] list of filenames defining the files in
            traces
        """
        if taskid == 0:
            self.logger.debug("preparing files for gradient evaluation")

        # Process all the stations for a given event using Pyaflowa
        io, config, pyaflowa = self.setup_pyaflowa(source_name)
        misfit = pyaflowa.process_event(io, config)

        if misfit is None:
            print(msg.cli(f"Event {source_name} returned no misfit, you may "
                          f"want to check waveform comparison parameters or "
                          f"discard this event from your workflow", 
                          header="preprocessing error", border="="))
            sys.exit(-1)

        # Event misfit defined by Tape et al. (2010) written to solver dir.
        self.write_residuals(path=cwd, scaled_misfit=misfit)

    def setup_pyaflowa(self, source_name):
        """
        A convenience function to set up a Pyaflowa processing instance.
        Called by prepare_eval_grad but also useful for debugging and manual
        processing
        """
        # Late import because preprocess is loaded before optimize,
        # Optimize required to know which iteration/step_count we are at
        optimize = sys.modules["seisflows_optimize"]

        iteration = optimize.iter
        step_count = optimize.line_search.step_count
        fix_windows = self.check_fix_windows(iteration, step_count)

        pyaflowa = self.pyaflowa.copy()
        io, config = pyaflowa.setup(source_name=source_name,
                                    iteration=iteration, step_count=step_count,
                                    fix_windows=fix_windows, 
                                    source_prefix=PAR.SOURCE_PREFIX)
        
        return io, config, pyaflowa

    def finalize(self):
        """
        Run some serial finalization tasks specific to Pyatoa, which will help
        aggregate the collection of output information:
            - Aggregate misfit windows using the Inspector class
            - Generate PDFs of waveform figures for easy access
            - Snapshot HDF5 files in a separate directory
        """
        unix.cd(self.path_datasets)
        insp = pyatoa.Inspector(PAR.TITLE, verbose=False)
        insp.discover()
        insp.save() 

        self.make_final_pdfs()

        if PAR.SNAPSHPOT:
            self.snapshot()

    def write_residuals(self, path, scaled_misfit):
        """
        Computes residuals and saves them to a text file in the appropriate path

        :type path: str        
        :param path: scratch directory path, e.g. PATH.GRAD or PATH.FUNC
        :type scaled_misfit: float
        :param scaled_misfit: the summation of misfit from each 
            source-receiver pair calculated by prepare_eval_grad()
        :type source_name: str
        :param source_name: name of the source related to the misfit, used
            for file naming
        """
        residuals_file = os.path.join(path, "residuals")        
        np.savetxt(residuals_file, [scaled_misfit], fmt="%11.6e")

    def sum_residuals(self, files):
        """
        Averages the event misfits and returns the total misfit.
        Total misfit defined by Tape et al. (2010)

        :type files: str
        :param files: list of single-column text files containing residuals
            that will have been generated using prepare_eval_grad()
        :rtype: float
        :return: average misfit
        """
        if len(files) != PAR.NTASK:
            print(msg.cli(f"Pyatoa preprocessing module did not recover the "
                          f"correct number of residual files "
                          f"({len(files)}/{PAR.NTASK}). Please check that "
                          f"the preprocessing logs", header="error")
                  )
            sys.exit(-1)

        total_misfit = 0
        for filename in files:
            total_misfit += np.sum(np.loadtxt(filename))

        total_misfit /= PAR.NTASK

        return total_misfit

    def check_fix_windows(self, iteration, step_count):
        """
        Determine how to address re-using misfit windows during  

        Options for PAR.FIX_WINDOWS:
            True: Always fix windows except for i01s00 because we don't have any
                  windows for the first function evaluation
            False: Don't fix windows, always choose a new set of windows
            Iter: Pick windows only on the initial step count (0th) for each
                  iteration. WARNING - does not work well with Thrifty Inversion
                  because the 0th step count is usually skipped
            Once: Pick new windows on the first function evaluation and then fix
                  windows. Useful for when parameters have changed, e.g. filter
                  bounds

        :rtype: bool
        :return: bool on whether to use windows from the previous step
        """
        # First function evaluation never fixes windows
        if iteration == 1 and step_count == 0:
            fix_windows = False
        elif isinstance(PAR.FIX_WINDOWS, str):
            # By 'iter'ation only pick new windows on the first step count
            if PAR.FIX_WINDOWS.upper() == "ITER":
                if step_count == 0:
                    fix_windows = False
                else:
                    fix_windows = True
            # 'Once' picks windows only for the first function evaluation of 
            # the current set of iterations.
            elif PAR.FIX_WINDOWS.upper() == "ONCE":
                if iteration == PAR.BEGIN and step_count == 0:
                    fix_windows = False
                else:
                    fix_windows = True
        # Bool fix windows simply sets the parameter
        elif isinstance(PAR.FIX_WINDOWS, bool):
            fix_windows = PAR.FIX_WINDOWS

        return fix_windows

    def snapshot(self):
        """
        Copy all ASDFDataSets in the data directory into a separate snapshot
        directory for a safeguard against HDF5 file corruption
        """
        snapshot_dir = os.path.join(self.path_datasets, "snapshot")
        if not os.path.exists(snapshot_dir):
            unix.mkdir(snapshot_dir)

        srcs = glob(os.path.join(self.path_datasets, "*.h5"))
        for src in srcs:
            dst = os.path.join(snapshot_dir, os.path.basename(src))
            unix.cp(src, dst)

    def make_final_pdfs(self):
        """
        Utility function to combine all pdfs for a given event, iteration, and
        step count into a single pdf. To reduce on file count and provide easier
        visualization. Removes the original event-based pdfs.

        TODO Can we shift this functinonality into Pyatoa?
        
        .. warning::
            This is a simple function because it won't account for missed 
            iterations i.e. if this isn't run in the finalization, it will 
            probably break the next time

        :raises AssertionError: When tags don't match the mainsolvers first tag
        """
        # Late import because preprocess is loaded before optimize
        solver = sys.modules["seisflows_solver"]

        # Relative pathing from here on out boys
        unix.cd(self.path_figures)
        sources = []
        for source_name in solver.source_names:
            sources += glob(os.path.join(source_name, "*.pdf"))

        # Incase this is run out of turn and pdfs were already deleted
        if not sources:
            return

        iter_steps = set([os.path.basename(_).split("_")[0] for _ in sources])
        for iter_step in iter_steps:
            # Merge pdfs that correspond to the same iteration and step count 
            fids = [_ for _ in sources if iter_step in _]
            merge_pdfs(fids=sorted(fids), fid_out=f"{iter_step}.pdf")
            unix.rm(fids)


