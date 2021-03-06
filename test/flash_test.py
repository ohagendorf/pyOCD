"""
 mbed CMSIS-DAP debugger
 Copyright (c) 2015 ARM Limited

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

     http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""

import argparse, os, sys
from time import sleep, time
from random import randrange
import math
import struct

parentdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parentdir)

import pyOCD
from pyOCD.board import MbedBoard
from pyOCD.target.cortex_m import float2int
from pyOCD.flash.flash import FLASH_PAGE_ERASE, FLASH_CHIP_ERASE
from test_util import Test, TestResult

addr = 0
size = 0

interface = None
board = None

import logging

class FlashTestResult(TestResult):
    def __init__(self):
        super(FlashTestResult, self).__init__(None, None, None)
        self.chip_erase_rate_erased = None
        self.page_erase_rate_same = None
        self.page_erase_rate = None
        self.analyze = None
        self.analyze_rate = None
        self.chip_erase_rate = None

class FlashTest(Test):
    def __init__(self):
        super(FlashTest, self).__init__("Flash Test", flash_test)

    def print_perf_info(self, result_list):
        result_list = filter(lambda x : isinstance(x, FlashTestResult), result_list)

        print("\r\n\r\n------ Analyzer Performance ------")
        print("{:<10}{:<12}{:<18}{:<18}".format("Target","Analyzer","Rate", "Time"))
        print("")
        for result in result_list:
            if result.passed:
                analyze_rate = "%f KB/s" % (result.analyze_rate / float(1000))
                analyze_time = "%s s" % result.analyze_time
            else:
                analyze_rate = "Fail"
                analyze_time = "Fail"
            print("{:<10}{:<12}{:<18}{:<18}".format(result.board.target_type, result.analyze, analyze_rate, analyze_time))
        print("")

        print("\r\n\r\n------ Test Rate ------")
        print("{:<10}{:<20}{:<20}{:<20}".format("Target","Chip Erase","Page Erase", "Page Erase (Same data)"))
        print("")
        for result in result_list:
            if result.passed:
                chip_erase_rate = "%f KB/s" % (result.chip_erase_rate / float(1000))
                page_erase_rate = "%f KB/s" % (result.page_erase_rate / float(1000))
                page_erase_rate_same = "%f KB/s" % (result.page_erase_rate_same / float(1000))
            else:
                chip_erase_rate = "Fail"
                page_erase_rate = "Fail"
                page_erase_rate_same = "Fail"
            print("{:<10}{:<20}{:<20}{:<20}".format(result.board.target_type, chip_erase_rate, page_erase_rate, page_erase_rate_same))
        print("")

    def run(self, board):
        try:
            result = self.test_function(board.getUniqueID())
        except Exception as e:
            result = FlashTestResult()
            result.passed = False
            print("Exception %s when testing board %s" % (e, board.getUniqueID()))
        result.board = board
        result.test = self
        return result


def same(d1, d2):
    if len(d1) != len(d2):
        return False
    for i in range(len(d1)):
        if d1[i] != d2[i]:
            return False
    return True


def flash_test(board_id):
    with MbedBoard.chooseBoard(board_id=board_id, frequency=1000000) as board:
        target_type = board.getTargetType()

        test_clock = 10000000
        if target_type == "kl25z":
            ram_start = 0x1ffff000
            ram_size = 0x4000
            rom_start = 0x00000000
            rom_size = 0x20000
        elif target_type == "kl46z":
            ram_start = 0x1fffe000
            ram_size = 0x8000
            rom_start = 0x00000000
            rom_size = 0x40000
        elif target_type == "k22f":
            ram_start = 0x1fff0000
            ram_size = 0x20000
            rom_start = 0x00000000
            rom_size = 0x80000
        elif target_type == "k64f":
            ram_start = 0x1FFF0000
            ram_size = 0x40000
            rom_start = 0x00000000
            rom_size = 0x100000
        elif target_type == "lpc11u24":
            ram_start = 0x10000000
            ram_size = 0x2000
            rom_start = 0x00000000
            rom_size = 0x8000
        elif target_type == "lpc1768":
            ram_start = 0x10000000
            ram_size = 0x8000
            rom_start = 0x00000000
            rom_size = 0x80000
        elif target_type == "lpc4330":
            ram_start = 0x10000000
            ram_size = 0x20000
            rom_start = 0x14000000
            rom_size = 0x100000
        elif target_type == "lpc800":
            ram_start = 0x10000000
            ram_size = 0x1000
            rom_start = 0x00000000
            rom_size = 0x4000
        elif target_type == "nrf51822":
            ram_start = 0x20000000
            ram_size = 0x4000
            rom_start = 0x00000000
            rom_size = 0x40000
            # Override clock since 10MHz is too fast
            test_clock = 1000000
        elif target_type == "maxwsnenv":
            ram_start = 0x20000000
            ram_size = 0x8000
            rom_start = 0x00000000
            rom_size = 0x40000
        elif target_type == "max32600mbed":
            ram_start = 0x20000000
            ram_size = 0x8000
            rom_start = 0x00000000
            rom_size = 0x40000
        else:
            raise Exception("The board is not supported by this test script.")

        target = board.target
        transport = board.transport
        flash = board.flash
        interface = board.interface

        transport.setClock(test_clock)
        transport.setDeferredTransfer(True)

        test_pass_count = 0
        test_count = 0
        result = FlashTestResult()

        def print_progress(progress):
            assert progress >= 0.0
            assert progress <= 1.0
            assert (progress == 0 and print_progress.prev_progress == 1.0) or (progress >= print_progress.prev_progress)

            # Reset state on 0.0
            if progress == 0.0:
                print_progress.prev_progress = 0
                print_progress.backwards_progress = False
                print_progress.done = False

            # Check for backwards progress
            if progress < print_progress.prev_progress:
                print_progress.backwards_progress = True
            print_progress.prev_progress = progress

            # print progress bar
            if not print_progress.done:
                sys.stdout.write('\r')
                i = int(progress*20.0)
                sys.stdout.write("[%-20s] %3d%%" % ('='*i, round(progress * 100)))

            # Finish on 1.0
            if progress >= 1.0:
                if not print_progress.done:
                    print_progress.done = True
                    sys.stdout.write("\n")
                    if print_progress.backwards_progress:
                        print("Progress went backwards during flash")
        print_progress.prev_progress = 0

        binary_file = os.path.join(parentdir, 'binaries', board.getTestBinary())
        with open(binary_file, "rb") as f:
            data = f.read()
        data = struct.unpack("%iB" % len(data), data)
        unused = rom_size - len(data)

        addr = rom_start
        size = len(data)

        print "\r\n\r\n------ Test Basic Page Erase ------"
        info = flash.flashBlock(addr, data, False, False, progress_cb = print_progress)
        data_flashed = target.readBlockMemoryUnaligned8(addr, size)
        if same(data_flashed, data) and info.program_type is FLASH_PAGE_ERASE:
            print("TEST PASSED")
            test_pass_count += 1
        else:
            print("TEST FAILED")
        test_count += 1

        print "\r\n\r\n------ Test Basic Chip Erase ------"
        info = flash.flashBlock(addr, data, False, True, progress_cb = print_progress)
        data_flashed = target.readBlockMemoryUnaligned8(addr, size)
        if same(data_flashed, data) and info.program_type is FLASH_CHIP_ERASE:
            print("TEST PASSED")
            test_pass_count += 1
        else:
            print("TEST FAILED")
        test_count += 1

        print "\r\n\r\n------ Test Smart Page Erase ------"
        info = flash.flashBlock(addr, data, True, False, progress_cb = print_progress)
        data_flashed = target.readBlockMemoryUnaligned8(addr, size)
        if same(data_flashed, data) and info.program_type is FLASH_PAGE_ERASE:
            print("TEST PASSED")
            test_pass_count += 1
        else:
            print("TEST FAILED")
        test_count += 1

        print "\r\n\r\n------ Test Smart Chip Erase ------"
        info = flash.flashBlock(addr, data, True, True, progress_cb = print_progress)
        data_flashed = target.readBlockMemoryUnaligned8(addr, size)
        if same(data_flashed, data) and info.program_type is FLASH_CHIP_ERASE:
            print("TEST PASSED")
            test_pass_count += 1
        else:
            print("TEST FAILED")
        test_count += 1

        print "\r\n\r\n------ Test Basic Page Erase (Entire chip) ------"
        new_data = list(data)
        new_data.extend(unused * [0x77])
        info = flash.flashBlock(0, new_data, False, False, progress_cb = print_progress)
        if info.program_type == FLASH_PAGE_ERASE:
            print("TEST PASSED")
            test_pass_count += 1
            result.page_erase_rate = float(len(new_data)) / float(info.program_time)
        else:
            print("TEST FAILED")
        test_count += 1

        print "\r\n\r\n------ Test Offset Write ------"
        new_data = [0x55] * board.flash.page_size * 2
        addr = rom_start + rom_size / 2
        info = flash.flashBlock(addr, new_data, progress_cb = print_progress)
        data_flashed = target.readBlockMemoryUnaligned8(addr, len(new_data))
        if same(data_flashed, new_data) and info.program_type is FLASH_PAGE_ERASE:
            print("TEST PASSED")
            test_pass_count += 1
        else:
            print("TEST FAILED")
        test_count += 1

        print "\r\n\r\n------ Test Multiple Block Writes ------"
        more_data = [0x33] * board.flash.page_size * 2
        addr = (rom_start + rom_size / 2) + 1 #cover multiple pages
        fb = flash.getFlashBuilder()
        fb.addData(rom_start, data)
        fb.addData(addr, more_data)
        fb.program(progress_cb = print_progress)
        data_flashed = target.readBlockMemoryUnaligned8(rom_start, len(data))
        data_flashed_more = target.readBlockMemoryUnaligned8(addr, len(more_data))
        if same(data_flashed, data) and same(data_flashed_more, more_data):
            print("TEST PASSED")
            test_pass_count += 1
        else:
            print("TEST FAILED")
        test_count += 1

        print "\r\n\r\n------ Test Overlapping Blocks ------"
        test_pass = False
        new_data = [0x33] * board.flash.page_size
        addr = (rom_start + rom_size / 2) #cover multiple pages
        fb = flash.getFlashBuilder()
        fb.addData(addr, new_data)
        try:
            fb.addData(addr + 1, new_data)
        except ValueError as e:
            print("Exception: %s" % e)
            test_pass = True
        if test_pass:
            print("TEST PASSED")
            test_pass_count += 1
        else:
            print("TEST FAILED")
        test_count += 1

        print "\r\n\r\n------ Test Empty Block Write ------"
        # Freebee if nothing asserts
        fb = flash.getFlashBuilder()
        fb.program()
        print("TEST PASSED")
        test_pass_count += 1
        test_count += 1

        print "\r\n\r\n------ Test Missing Progress Callback ------"
        # Freebee if nothing asserts
        addr = rom_start
        flash.flashBlock(rom_start, data, True)
        print("TEST PASSED")
        test_pass_count += 1
        test_count += 1


        # Note - The decision based tests below are order dependent since they
        # depend on the previous state of the flash

        print "\r\n\r\n------ Test Chip Erase Decision ------"
        new_data = list(data)
        new_data.extend([0xff] * unused) # Pad with 0xFF
        info = flash.flashBlock(0, new_data, progress_cb = print_progress)
        if info.program_type == FLASH_CHIP_ERASE:
            print("TEST PASSED")
            test_pass_count += 1
            result.chip_erase_rate_erased = float(len(new_data)) / float(info.program_time)
        else:
            print("TEST FAILED")
        test_count += 1

        print "\r\n\r\n------ Test Chip Erase Decision 2 ------"
        new_data = list(data)
        new_data.extend([0x00] * unused) # Pad with 0x00
        info = flash.flashBlock(0, new_data, progress_cb = print_progress)
        if info.program_type == FLASH_CHIP_ERASE:
            print("TEST PASSED")
            test_pass_count += 1
            result.chip_erase_rate = float(len(new_data)) / float(info.program_time)
        else:
            print("TEST FAILED")
        test_count += 1

        print "\r\n\r\n------ Test Page Erase Decision ------"
        new_data = list(data)
        new_data.extend([0x00] * unused) # Pad with 0x00
        info = flash.flashBlock(0, new_data, progress_cb = print_progress)
        if info.program_type == FLASH_PAGE_ERASE:
            print("TEST PASSED")
            test_pass_count += 1
            result.page_erase_rate_same = float(len(new_data)) / float(info.program_time)
            result.analyze = info.analyze_type
            result.analyze_time = info.analyze_time
            result.analyze_rate = float(len(new_data)) / float(info.analyze_time)
        else:
            print("TEST FAILED")
        test_count += 1

        print "\r\n\r\n------ Test Page Erase Decision 2 ------"
        new_data = list(data)
        size_same = unused * 5 / 6
        size_differ = unused - size_same
        new_data.extend([0x00] * size_same) # Pad 5/6 with 0x00 and 1/6 with 0xFF
        new_data.extend([0x55] * size_differ)
        info = flash.flashBlock(0, new_data, progress_cb = print_progress)
        if info.program_type == FLASH_PAGE_ERASE:
            print("TEST PASSED")
            test_pass_count += 1
        else:
            print("TEST FAILED")
        test_count += 1

        print("\r\n\r\nTest Summary:")
        print("Pass count %i of %i tests" % (test_pass_count, test_count))
        if test_pass_count == test_count:
            print("FLASH TEST SCRIPT PASSED")
        else:
            print("FLASH TEST SCRIPT FAILED")

        target.reset()

        result.passed = test_count == test_pass_count
        return result

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Set to debug to print some of the decisions made while flashing
    flash_test(None)