#!/usr/bin/env python
# coding=utf-8
"""
ccDecoder is a Python Closed Caption Decoder/Extractor
Presented by Max Smith and notonbluray.com

Python 3.7+ compatible

Public domain / Unlicense per license section below
But attribution is always appreciated where possible.

Usage
-----
cc_decoder.py somevideofile.mpg >> somevideofile.srt

A Few Notes
-----------

There are two scripts that make up this tool: A command line interface
(cc_decoder) and a library file (lib.cc_decode).

The library makes the assumption that a stream of images are passed to
it that have closed caption data embedded in the top. It also assumes
that the images are wrapped in a object which will provide standardized
access to it. The library has minimal dependencies, and may be useful
for embedded projects.

The command line interface has many dependencies including PIL (Pillow)
and FFmpeg. Excellent builds of FFMpeg are available at
http://ffmpeg.zeranoe.com/builds/

If you use the command line tool - it's worth providing it with access
to a fast temporary area for writing data to, by default it will use
the system default temporary area, which may share space with your OS.

My primary goal of this project was to extract subtitles from my
Laserdisc collection (mostly simple pop-on mode). I've tried to cover
as much of the Closed Caption spec as possible, but I am limited by a
shortage of test-cases. I would love to get it working on some more
exotic captions (i.e. roll-up, XDS, ITV) but really need some sample
media to work with. If you have media that has these more exotic
captions - please drop me a line via my website (notonbluray.com)
"""

__author__ = "Max Smith"
__copyright__ = "Copyright 2014 Max Smith"
__credits__ = ["Max Smith"]
__license__ = """
This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or
distribute this software, either in source code form or as a compiled
binary, for any purpose, commercial or non-commercial, and by any
means.

In jurisdictions that recognize copyright laws, the author or authors
of this software dedicate any and all copyright interest in the
software to the public domain. We make this dedication for the benefit
of the public at large and to the detriment of our heirs and
successors. We intend this dedication to be an overt act of
relinquishment in perpetuity of all present and future rights to this
software under copyright law.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.

For more information, please refer to <http://unlicense.org/>
"""

import os
import argparse
import shutil
import subprocess
import sys
import multiprocessing
from setproctitle import setproctitle
import time
import lib.cc_decode
from lib.cc_decode import (
    decode_to_srt,
    decode_captions_raw,
    decode_to_scc,
    decode_to_text,
    decode_captions_debug,
    extract_closed_caption_bytes,
    decode_xds_packets
)

import numpy as np


class ClosedCaptionFileDecoder(object):
    DECODERS = {'srt': decode_to_srt,
                'scc': decode_to_scc,
                'text': decode_to_text,
                'raw': decode_captions_raw,
                'debug': decode_captions_debug,
                'xds': decode_xds_packets}

    def __init__(self, ffmpeg_path, ffmpeg_pre_scale, ffmpeg_hw_accel, deinterlaced, ccformat, start_line, end_line, quiet):
        self.ffmpeg_path = ffmpeg_path
        self.ffmpeg_pre_scale = ffmpeg_pre_scale
        self.ffmpeg_hw_accel = ffmpeg_hw_accel
        self.deinterlaced = deinterlaced
        self.format = ccformat or 'srt'
        self.fpid = None

        self.start_line = start_line
        self.end_line = end_line

        if self.start_line > self.end_line:
            raise Exception("`start_line` cannot be greater than `end_line`")

        self.workingdir = ''
        self.quiet = quiet

        self.image_width = 720
        self.image_height = self.end_line + 1
        self.caption_count = 0
        self.frame_count = 0

    @staticmethod
    def print_status_worker(rx):
        setproctitle(multiprocessing.current_process().name)

        message = ""
        max_first_row_len = 0
        first_row_len = 0
        code_count = 0

        # rate tracking
        prev_row_ts = time.perf_counter_ns()
        curr_row_ts = prev_row_ts
        prev_row_frame = 0
        frames_per_second = 29.97
        decode_rate = 0

        while True:
            try:
                data = rx.recv()
                if data == "DONE":
                    break
            except:
                break
    
            frame, rows = data

            curr_row_ts = time.perf_counter_ns()
            elapsed_seconds = (curr_row_ts - prev_row_ts) / 1e9
            if elapsed_seconds >= 1:
                decode_rate = (frame - prev_row_frame) / frames_per_second / elapsed_seconds
                prev_row_frame = frame
                prev_row_ts = curr_row_ts

            print(" " * len(message) + "\r", end="", file=sys.stderr)
            message = f"Frame: {frame} | Code Count: {code_count} | Rate: {decode_rate:.2f}x"

            for i, (row_num, code, control, b1, _, b2, _) in enumerate(rows):
                if i == 1:
                    # pad message to consistent width
                    message = message + " " * (max_first_row_len - first_row_len)
    
                message = message + f" | Line: {row_num} | Control: {'True ' if control else 'False'} | Byte1: {b1:#04x} | Byte2: {b2:#04x} {'| ' + code if code != "" else ""}"

                if i == 0:
                    first_row_len = len(message)
                    if first_row_len > max_first_row_len:
                        max_first_row_len = first_row_len

                code_count += 1

            print(message, end="\r", file=sys.stderr)

    @staticmethod
    def image_decoder_worker(
            tx,
            image_width,
            image_height,
            input_file,
            ffmpeg_path,
            ffmpeg_pre_scale,
            ffmpeg_hw_accel,
            deinterlaced,
            start_line,
            end_line
    ):
        setproctitle(multiprocessing.current_process().name)
        try:
            if not os.path.exists(ffmpeg_path):
                raise RuntimeError('Could not find ffmpeg at %s' % ffmpeg_path)

            image_size = image_width * image_height
            search_lines = end_line - start_line

            ffmpeg_cmd = [
                ffmpeg_path,
                "-loglevel", "error",
                "-hwaccel_device", "/dev/dri/renderD129",
                *(["-hwaccel", ffmpeg_hw_accel] if ffmpeg_hw_accel else []),
                "-i", input_file,
                "-vf", f"format=gray8,{ffmpeg_pre_scale + ',' if ffmpeg_pre_scale else ''}scale={image_width}:-1:flags=neighbor{',interlace=lowpass=off' if deinterlaced else ''},crop=iw:{image_height}:0:0",
                "-f", "rawvideo",
                "-pix_fmt", "gray8",
                "pipe:1"
            ]

            fpid = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=sys.stderr,
                bufsize=image_size
            )

            lib.cc_decode.PRE_COMPUTED_SINE_TEMPLATES = lib.cc_decode.precompute_sine_templates(image_width)

            while True:
                image_buffer = fpid.stdout.read(image_size)
                if len(image_buffer) < image_size:
                    break

                image = np.frombuffer(image_buffer, dtype=np.uint8).reshape(image_height, image_width)

                tx.send(extract_closed_caption_bytes(image, start_line, search_lines))
        except (InterruptedError, KeyboardInterrupt, EOFError):
            pass
        finally:
            tx.send("DONE")

    def decode(self, filename, output_filename):
        running_decoders = []
        running_decoders_conns = []
        formats = self.format.split(",")
        options = {}

        # start decoders
        for format in formats:
            if format in self.DECODERS:
                decoder_func = self.DECODERS.get(format)

                rx, tx = multiprocessing.Pipe(False)
                decoder = multiprocessing.Process(None, decoder_func, name=f"cc_decoder_{format}", args=(rx,output_filename,options,))
                decoder.start()
                running_decoders.append(decoder)
                running_decoders_conns.append(tx)

        exception = None

        if len(running_decoders) > 0:
            print("Decoding captions...", file=sys.stderr)
            
            # start ffmpeg and image decoding
            row_rx, row_tx = multiprocessing.Pipe(False)
            image_decoder_process = multiprocessing.Process(
                None, ClosedCaptionFileDecoder.image_decoder_worker, name=f"cc_decoder_image_decoder",
                args=(
                    row_tx,
                    self.image_width,
                    self.image_height,
                    filename,
                    self.ffmpeg_path,
                    self.ffmpeg_pre_scale,
                    self.ffmpeg_hw_accel,
                    self.deinterlaced,
                    self.start_line,
                    self.end_line + 1,
                )
            )
            image_decoder_process.start()

            if not self.quiet:
                status_rx, status_tx = multiprocessing.Pipe(False)
                print_status_process = multiprocessing.Process(
                    None, ClosedCaptionFileDecoder.print_status_worker, name=f"cc_decoder_print_status",
                    args=(status_rx,)
                )
                print_status_process.start()

            try:
                while True:
                    try:
                        rows = row_rx.recv()
                        if rows == "DONE":
                            break

                        # send decoded data to all decoder processes
                        for conn in running_decoders_conns:
                            conn.send(rows)

                        # send data to status process
                        if not self.quiet:
                            status_tx.send((self.frame_count, rows))

                        self.frame_count += 1
                    except (InterruptedError, KeyboardInterrupt, EOFError):
                        break
            except Exception as e:
                exception = e
            finally:
                # clean up decoder processes
                for i in range(len(running_decoders_conns)):
                    try:
                        running_decoders_conns[i].send("DONE")
                    except:
                        running_decoders[i].terminate()
                
                for decoder in running_decoders:
                    decoder.join()
                
                # clean up status output
                if not self.quiet:
                    try:
                        status_tx.send("DONE")
                    except:
                        print_status_process.terminate()
                    print_status_process.join()

            if exception is not None:
                print("", file=sys.stderr)
                print("Error decoding, check the exception above.", file=sys.stderr)
                raise exception
            else:
                print("", file=sys.stderr)
                print("Done!", file=sys.stderr)
                return 0
        else:
            raise RuntimeError('Unknown output format %s, try one of %s' % (format, self.DECODERS.keys()))

def main():
    p = argparse.ArgumentParser(
        description='Extracts CEA-608-E Closed Captions (line 21) data from a video file',
        formatter_class=argparse.RawTextHelpFormatter,
    )

    ffmpeg = shutil.which("ffmpeg")
    p.add_argument('videofile', help='Input video file name')
    output_options = p.add_argument_group('Output Options')
    output_options.add_argument('-o', metavar='', required=True, help='Output subtitle filename without extension')

    p.add_argument('-q', default=False, action='store_true', help='Suppress status output')

    input_video_options = p.add_argument_group('Input Options')
    input_video_options.add_argument('--deinterlaced', default=False, action='store_true', help='Specify if the input video is progressive (i.e. de-interlaced)')
    input_video_options.add_argument('--ffmpeg', metavar='', default=ffmpeg, help='Override the default path to the ffmpeg binary (default %s)' % ffmpeg)
    input_video_options.add_argument('--ffmpeg_pre_scale', metavar='', default=None, help='FFMpeg video filter options before scaling.')
    input_video_options.add_argument('--ffmpeg_hw_accel', metavar='', default=None, help='FFMpeg `hwaccel` option (i.e. none,auto,vaapi,nvdec,etc...) (default none)')

    output_options.add_argument('--ccformat', metavar='', default='srt', 
                                help=(
                                    'Specify one or more comma separated output formats (e.g. srt,scc,text) \n'
                                    '  srt   - SubRip subtitles (default)\n'
                                    '  scc   - Scenarist Closed Captions\n'
                                    '  text  - Plain text output (TEXT mode only)\n'
                                    '  xds   - eXtended Data Services (XDS) data\n'
                                    '  raw   - Raw caption data\n'
                                    '  debug - Debug output'
                                )
    )

    decoding_options = p.add_argument_group('Decoding Options')
    decoding_options.add_argument('--start_line', metavar='', default=0, type=int, help='Start at `start_line` when searching through the video 0=topmost line (default 0)')
    decoding_options.add_argument('--end_line', metavar='', default=10, type=int, help='End at `end_line` when searching through the video (default 10)')

    args = p.parse_args()

    if args.videofile:
        decoder = ClosedCaptionFileDecoder(ffmpeg_path=args.ffmpeg,
                                           ffmpeg_pre_scale=args.ffmpeg_pre_scale,
                                           ffmpeg_hw_accel=args.ffmpeg_hw_accel,
                                           deinterlaced=args.deinterlaced,
                                           ccformat=args.ccformat,
                                           start_line=args.start_line,
                                           end_line=args.end_line,
                                           quiet=args.q)
        exit(decoder.decode(args.videofile, args.o))

if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()