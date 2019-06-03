# Copyright (c) 2019 Aiven, Helsinki, Finland. https://aiven.io/
import os

import myhoard.util as myhoard_util
import pytest
from myhoard.basebackup_operation import BasebackupOperation

from . import build_statsd_client

pytestmark = [pytest.mark.unittest, pytest.mark.all]


def test_basic_backup(mysql_master):
    with myhoard_util.mysql_cursor(**mysql_master["connect_options"]) as cursor:
        for db_index in range(15):
            cursor.execute(f"CREATE DATABASE test{db_index}")
            cursor.execute(f"CREATE TABLE test{db_index}.foo{db_index} (id integer primary key)")
            for value in range(15):
                cursor.execute(f"INSERT INTO test{db_index}.foo{db_index} (id) VALUES ({value})")
        cursor.execute("show master status")
        master_status = cursor.fetchone()

    backed_up_files = set()

    def progress_callback(
        *,
        estimated_progress,
        estimated_total_bytes,
        last_file_name,
        last_file_size,
        processed_original_bytes,
    ):
        if last_file_name:
            backed_up_files.add(last_file_name)

    bytes_read = [0]

    def stream_handler(stream):
        while True:
            data = stream.read(10 * 1024)
            if not data:
                break
            else:
                bytes_read[0] += len(data)

    encryption_key = os.urandom(24)
    op = BasebackupOperation(
        encryption_algorithm="AES256",
        encryption_key=encryption_key,
        mysql_client_params=mysql_master["connect_options"],
        mysql_config_file_name=mysql_master["config_name"],
        mysql_data_directory=mysql_master["config_options"]["datadir"],
        progress_callback=progress_callback,
        stats=build_statsd_client(),
        stream_handler=stream_handler,
        temp_dir=mysql_master["base_dir"],
    )
    op.create_backup()

    for db_index in range(15):
        assert f"./test{db_index}/foo{db_index}.ibd" in backed_up_files

    assert op.binlog_info["gtid"] == master_status["Executed_Gtid_Set"]

    # Taking basebackup might flush binary logs
    with myhoard_util.mysql_cursor(**mysql_master["connect_options"]) as cursor:
        cursor.execute("SHOW MASTER STATUS")
        master_status = cursor.fetchone()

    assert op.binlog_info["file_name"] == master_status["File"]
    assert op.binlog_info["file_position"] == master_status["Position"]

    # Even almost empty backup is a few megs due to standard files that are always included
    assert bytes_read[0] > 2 * 1024 * 1024


def test_stream_handler_error_is_propagated(mysql_master):
    def stream_handler(_stream):
        raise ValueError("This is test error")

    encryption_key = os.urandom(24)
    op = BasebackupOperation(
        encryption_algorithm="AES256",
        encryption_key=encryption_key,
        mysql_client_params=mysql_master["connect_options"],
        mysql_config_file_name=mysql_master["config_name"],
        mysql_data_directory=mysql_master["config_options"]["datadir"],
        stats=build_statsd_client(),
        stream_handler=stream_handler,
        temp_dir=mysql_master["base_dir"],
    )
    with pytest.raises(ValueError, match="^This is test error$"):
        op.create_backup()


def test_fails_on_invalid_params(mysql_master):
    def stream_handler(_stream):
        pass

    op = BasebackupOperation(
        encryption_algorithm="nosuchalgo",
        encryption_key=os.urandom(24),
        mysql_client_params={
            "host": "127.0.0.1",
        },
        mysql_config_file_name=mysql_master["config_name"],
        mysql_data_directory=mysql_master["config_options"]["datadir"],
        stats=build_statsd_client(),
        stream_handler=stream_handler,
        temp_dir=mysql_master["base_dir"],
    )
    with pytest.raises(Exception, match="^xtrabackup failed with code 13$"):
        op.create_backup()
