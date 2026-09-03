#pragma once

#include <functional>
#include <map>
#include <string>
#include <vector>

namespace BBL {

using OnPrinterConnectedFn = std::function<void(std::string topic_str)>;
using OnLocalConnectedFn = std::function<void(int status, std::string dev_id, std::string msg)>;
using OnServerConnectedFn = std::function<void(int return_code, int reason_code)>;
using OnMessageFn = std::function<void(std::string dev_id, std::string msg)>;
using OnHttpErrorFn = std::function<void(unsigned http_code, std::string http_body)>;
using GetCountryCodeFn = std::function<std::string()>;
using GetSubscribeFailureFn = std::function<void(std::string topic)>;
using OnUpdateStatusFn = std::function<void(int status, int code, std::string msg)>;
using WasCancelledFn = std::function<bool()>;
using OnMsgArrivedFn = std::function<void(std::string dev_info_json_str)>;
using QueueOnMainFn = std::function<void(std::function<void()>)>;
using OnServerErrFn = std::function<void(std::string url, int status)>;


enum SendingPrintJobStage {
    PrintingStageCreate = 0,
    PrintingStageUpload = 1,
    PrintingStageWaiting = 2,
    PrintingStageSending = 3,
    PrintingStageRecord = 4,
    PrintingStageWaitPrinter = 5,
    PrintingStageFinished = 6,
    PrintingStageERROR = 7,
    PrintingStageLimit = 8,
};

enum ConnectStatus {
    ConnectStatusOk = 0,
    ConnectStatusFailed = 1,
    ConnectStatusLost = 2,
};

struct detectResult {
    std::string result_msg;
    std::string command;
    std::string dev_id;
    std::string model_id;
    std::string dev_name;
    std::string version;
    std::string bind_state;
    std::string connect_type;
};

// Exact ABI layout for Bambu networking ABI 02.07.01.x.
struct PrintParams {
    std::string dev_id;
    std::string task_name;
    std::string project_name;
    std::string preset_name;
    std::string filename;
    std::string config_filename;
    int plate_index;
    std::string ftp_folder;
    std::string ftp_file;
    std::string ftp_file_md5;
    std::string nozzle_mapping;
    std::string ams_mapping;
    std::string ams_mapping2;
    std::string ams_mapping_info;
    std::string nozzles_info;
    std::string connection_type;
    std::string comments;
    int origin_profile_id = 0;
    int stl_design_id = 0;
    std::string origin_model_id;
    std::string print_type;
    std::string dst_file;
    std::string dev_name;

    std::string dev_ip;
    bool use_ssl_for_ftp;
    bool use_ssl_for_mqtt;
    std::string username;
    std::string password;

    bool task_bed_leveling;
    bool task_flow_cali;
    bool task_vibration_cali;
    bool task_layer_inspect;
    bool task_record_timelapse;
    bool task_timelapse_use_internal;
    bool task_use_ams;
    std::string task_bed_type;
    std::string extra_options;
    int auto_bed_leveling{0};
    int auto_flow_cali{0};
    int auto_offset_cali{0};
    int extruder_cali_manual_mode{-1};
    bool task_ext_change_assist;
    bool try_emmc_print;
    std::string svc_context;
};

} // namespace BBL

using func_get_version = std::string (*)(void);
using func_create_agent = void* (*)(std::string log_dir);
using func_destroy_agent = int (*)(void* agent);
using func_init_log = int (*)(void* agent);
using func_set_config_dir = int (*)(void* agent, std::string config_dir);
using func_set_cert_file = int (*)(void* agent, std::string folder, std::string filename);
using func_set_country_code = int (*)(void* agent, std::string country_code);
using func_start = int (*)(void* agent);
using func_set_on_ssdp_msg_fn = int (*)(void* agent, BBL::OnMsgArrivedFn fn);
using func_set_on_printer_connected_fn = int (*)(void* agent, BBL::OnPrinterConnectedFn fn);
using func_set_on_server_connected_fn = int (*)(void* agent, BBL::OnServerConnectedFn fn);
using func_set_on_http_error_fn = int (*)(void* agent, BBL::OnHttpErrorFn fn);
using func_set_get_country_code_fn = int (*)(void* agent, BBL::GetCountryCodeFn fn);
using func_set_on_subscribe_failure_fn = int (*)(void* agent, BBL::GetSubscribeFailureFn fn);
using func_set_on_message_fn = int (*)(void* agent, BBL::OnMessageFn fn);
using func_set_on_user_message_fn = int (*)(void* agent, BBL::OnMessageFn fn);
using func_set_on_local_connect_fn = int (*)(void* agent, BBL::OnLocalConnectedFn fn);
using func_set_on_local_message_fn = int (*)(void* agent, BBL::OnMessageFn fn);
using func_set_queue_on_main_fn = int (*)(void* agent, BBL::QueueOnMainFn fn);
using func_set_server_callback = int (*)(void* agent, BBL::OnServerErrFn fn);
using func_enable_multi_machine = void (*)(void* agent, bool enable);
using func_start_discovery = bool (*)(void* agent, bool start, bool sending);
using func_change_user = int (*)(void* agent, std::string user_info);
using func_bind_detect = int (*)(void* agent, std::string dev_ip, std::string sec_link, BBL::detectResult& detect);
using func_connect_printer = int (*)(void* agent, std::string dev_id, std::string dev_ip, std::string username, std::string password, bool use_ssl);
using func_disconnect_printer = int (*)(void* agent);
using func_start_subscribe = int (*)(void* agent, std::string module);
using func_send_message_to_printer = int (*)(void* agent, std::string dev_id, std::string json_str, int qos, int flag);
using func_install_device_cert = void (*)(void* agent, std::string dev_id, bool lan_only);
using func_set_extra_http_header = int (*)(void* agent, std::map<std::string, std::string> extra_headers);
using func_start_local_print = int (*)(void* agent, BBL::PrintParams params, BBL::OnUpdateStatusFn update_fn, BBL::WasCancelledFn cancel_fn);
