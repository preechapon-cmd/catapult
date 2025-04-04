#!/usr/bin/env python
# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# pylint: disable=protected-access

import itertools
import threading
import time
import unittest

from unittest import mock

import six

from devil.android import logcat_monitor
from devil.android.sdk import adb_wrapper


def _CreateTestLog(raw_logcat=None):
  test_adb = adb_wrapper.AdbWrapper('0123456789abcdef')
  test_adb.test_stop_nonce = None

  def mock_shell(cmd, *_args, **_kwargs):
    assert cmd.startswith('log '), cmd
    assert test_adb.test_stop_nonce is None
    test_adb.test_stop_nonce = cmd[4:]
    return ''

  def mock_logcat(*_args, **_kwargs):
    for l in raw_logcat:
      yield l
    yield None
    while test_adb.test_stop_nonce is None:
      time.sleep(.001)
    yield 'prefix: ' + test_adb.test_stop_nonce
    yield 'Should not be included.'

  test_adb.Logcat = mock_logcat
  test_adb.Shell = mock_shell
  test_log = logcat_monitor.LogcatMonitor(test_adb, clear=False)
  return test_log


def zip_longest(expected, actual):
  # pylint: disable=no-member
  if six.PY2:
    return itertools.izip_longest(expected, actual)
  return itertools.zip_longest(expected, actual)


class LogcatMonitorTest(unittest.TestCase):

  _TEST_THREADTIME_LOGCAT_DATA = [
      '01-01 01:02:03.456  7890  0987 V LogcatMonitorTest: '
      'verbose logcat monitor test message 1',
      '01-01 01:02:03.457  8901  1098 D LogcatMonitorTest: '
      'debug logcat monitor test message 2',
      '01-01 01:02:03.458  9012  2109 I LogcatMonitorTest: '
      'info logcat monitor test message 3',
      '01-01 01:02:03.459  0123  3210 W LogcatMonitorTest: '
      'warning logcat monitor test message 4',
      '01-01 01:02:03.460  1234  4321 E LogcatMonitorTest: '
      'error logcat monitor test message 5',
      '01-01 01:02:03.461  2345  5432 F LogcatMonitorTest: '
      'fatal logcat monitor test message 6',
      '01-01 01:02:03.462  3456  6543 D LogcatMonitorTest: '
      'last line'
  ]

  def assertIterEqual(self, expected_iter, actual_iter):
    for expected, actual in zip_longest(expected_iter, actual_iter):
      self.assertIsNotNone(
          expected,
          msg='actual has unexpected elements starting with %s' % str(actual))
      self.assertIsNotNone(
          actual,
          msg='actual is missing elements starting with %s' % str(expected))
      self.assertEqual(actual.group('proc_id'), expected[0])
      self.assertEqual(actual.group('thread_id'), expected[1])
      self.assertEqual(actual.group('log_level'), expected[2])
      self.assertEqual(actual.group('component'), expected[3])
      self.assertEqual(actual.group('message'), expected[4])

    with self.assertRaises(StopIteration):
      next(actual_iter)
    with self.assertRaises(StopIteration):
      next(expected_iter)

  @mock.patch('time.sleep', mock.Mock())
  def testWaitFor_success(self):
    test_log = _CreateTestLog(
        raw_logcat=type(self)._TEST_THREADTIME_LOGCAT_DATA)
    test_log.Start()
    actual_match = test_log.WaitFor(r'.*(fatal|error) logcat monitor.*', None)
    self.assertTrue(actual_match)
    self.assertEqual(
        '01-01 01:02:03.460  1234  4321 E LogcatMonitorTest: '
        'error logcat monitor test message 5', actual_match.group(0))
    self.assertEqual('error', actual_match.group(1))
    test_log.Stop()
    test_log.Close()

  @mock.patch('time.sleep', mock.Mock())
  def testWaitFor_failure(self):
    test_log = _CreateTestLog(
        raw_logcat=type(self)._TEST_THREADTIME_LOGCAT_DATA)
    test_log.Start()
    actual_match = test_log.WaitFor(r'.*My Success Regex.*',
                                    r'.*(fatal|error) logcat monitor.*')
    self.assertIsNone(actual_match)
    test_log.Stop()
    test_log.Close()

  @mock.patch('time.sleep', mock.Mock())
  def testWaitFor_buffering(self):
    # Simulate an adb log stream which does not complete until the test tells it
    # to. This checks that the log matcher can receive individual lines from the
    # log reader thread even if adb is not producing enough output to fill an
    # entire file io buffer.
    finished_lock = threading.Lock()
    finished_lock.acquire()

    def LogGenerator():
      for line in type(self)._TEST_THREADTIME_LOGCAT_DATA:
        yield line
      finished_lock.acquire()
      yield '01-01 01:02:03.456  7890  0987 V LogcatMonitorTest: extra line'

    test_log = _CreateTestLog(raw_logcat=LogGenerator())
    test_log.Start()

    actual_match = test_log.WaitFor(r'.*last line.*', None)
    self.assertTrue(actual_match)
    finished_lock.release()
    test_log.Stop()

    actual_results = test_log.FindAll(r'.*')
    self.assertEqual('extra line', list(actual_results)[-1].group('message'))

    test_log.Close()

  @mock.patch('time.sleep', mock.Mock())
  def testFindAll_defaults(self):
    test_log = _CreateTestLog(
        raw_logcat=type(self)._TEST_THREADTIME_LOGCAT_DATA)
    test_log.Start()
    test_log.WaitFor(r'.*last line.*', None)
    test_log.Stop()
    expected_results = [('7890', '0987', 'V', 'LogcatMonitorTest',
                         'verbose logcat monitor test message 1'),
                        ('8901', '1098', 'D', 'LogcatMonitorTest',
                         'debug logcat monitor test message 2'),
                        ('9012', '2109', 'I', 'LogcatMonitorTest',
                         'info logcat monitor test message 3'),
                        ('0123', '3210', 'W', 'LogcatMonitorTest',
                         'warning logcat monitor test message 4'),
                        ('1234', '4321', 'E', 'LogcatMonitorTest',
                         'error logcat monitor test message 5'),
                        ('2345', '5432', 'F', 'LogcatMonitorTest',
                         'fatal logcat monitor test message 6')]
    actual_results = test_log.FindAll(r'\S* logcat monitor test message \d')
    self.assertIterEqual(iter(expected_results), actual_results)
    test_log.Close()

  @mock.patch('time.sleep', mock.Mock())
  def testFindAll_defaults_miss(self):
    test_log = _CreateTestLog(
        raw_logcat=type(self)._TEST_THREADTIME_LOGCAT_DATA)
    test_log.Start()
    test_log.WaitFor(r'.*last line.*', None)
    test_log.Stop()
    expected_results = []
    actual_results = test_log.FindAll(r'\S* nothing should match this \d')
    self.assertIterEqual(iter(expected_results), actual_results)
    test_log.Close()

  @mock.patch('time.sleep', mock.Mock())
  def testFindAll_filterProcId(self):
    test_log = _CreateTestLog(
        raw_logcat=type(self)._TEST_THREADTIME_LOGCAT_DATA)
    test_log.Start()
    test_log.WaitFor(r'.*last line.*', None)
    test_log.Stop()
    actual_results = test_log.FindAll(
        r'\S* logcat monitor test message \d', proc_id=1234)
    expected_results = [('1234', '4321', 'E', 'LogcatMonitorTest',
                         'error logcat monitor test message 5')]
    self.assertIterEqual(iter(expected_results), actual_results)
    test_log.Close()

  @mock.patch('time.sleep', mock.Mock())
  def testFindAll_filterThreadId(self):
    test_log = _CreateTestLog(
        raw_logcat=type(self)._TEST_THREADTIME_LOGCAT_DATA)
    test_log.Start()
    test_log.WaitFor(r'.*last line.*', None)
    test_log.Stop()
    actual_results = test_log.FindAll(
        r'\S* logcat monitor test message \d', thread_id=2109)
    expected_results = [('9012', '2109', 'I', 'LogcatMonitorTest',
                         'info logcat monitor test message 3')]
    self.assertIterEqual(iter(expected_results), actual_results)
    test_log.Close()

  @mock.patch('time.sleep', mock.Mock())
  def testFindAll_filterLogLevel(self):
    test_log = _CreateTestLog(
        raw_logcat=type(self)._TEST_THREADTIME_LOGCAT_DATA)
    test_log.Start()
    test_log.WaitFor(r'.*last line.*', None)
    test_log.Stop()
    actual_results = test_log.FindAll(
        r'\S* logcat monitor test message \d', log_level=r'[DW]')
    expected_results = [('8901', '1098', 'D', 'LogcatMonitorTest',
                         'debug logcat monitor test message 2'),
                        ('0123', '3210', 'W', 'LogcatMonitorTest',
                         'warning logcat monitor test message 4')]
    self.assertIterEqual(iter(expected_results), actual_results)
    test_log.Close()

  @mock.patch('time.sleep', mock.Mock())
  def testFindAll_filterComponent(self):
    test_log = _CreateTestLog(
        raw_logcat=type(self)._TEST_THREADTIME_LOGCAT_DATA)
    test_log.Start()
    test_log.WaitFor(r'.*last line.*', None)
    test_log.Stop()
    actual_results = test_log.FindAll(r'.*', component='LogcatMonitorTest')
    expected_results = [('7890', '0987', 'V', 'LogcatMonitorTest',
                         'verbose logcat monitor test message 1'),
                        ('8901', '1098', 'D', 'LogcatMonitorTest',
                         'debug logcat monitor test message 2'),
                        ('9012', '2109', 'I', 'LogcatMonitorTest',
                         'info logcat monitor test message 3'),
                        ('0123', '3210', 'W', 'LogcatMonitorTest',
                         'warning logcat monitor test message 4'),
                        ('1234', '4321', 'E', 'LogcatMonitorTest',
                         'error logcat monitor test message 5'),
                        ('2345', '5432', 'F', 'LogcatMonitorTest',
                         'fatal logcat monitor test message 6'),
                        ('3456', '6543', 'D', 'LogcatMonitorTest', 'last line')]
    self.assertIterEqual(iter(expected_results), actual_results)
    test_log.Close()


if __name__ == '__main__':
  unittest.main(verbosity=2)
