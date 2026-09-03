#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef _CRT_SECURE_NO_WARNINGS
#define _CRT_SECURE_NO_WARNINGS
#endif
#include <windows.h>
#endif

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <filesystem>
#include <functional>
#include <iostream>
#include <map>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "bambu_networking_020701.hpp"

namespace fs = std::filesystem;
using namespace std::chrono_literals;

namespace {

std::string json_escape(const std::string& s) {
    std::ostringstream o;
    for (unsigned char c : s) {
        switch (c) {
        case '\\': o << "\\\\"; break;
        case '"': o << "\\\""; break;
        case '\b': o << "\\b"; break;
        case '\f': o << "\\f"; break;
        case '\n': o << "\\n"; break;
        case '\r': o << "\\r"; break;
        case '\t': o << "\\t"; break;
        default:
            if (c < 0x20) {
                const char* hex = "0123456789abcdef";
                o << "\\u00" << hex[(c >> 4) & 0xf] << hex[c & 0xf];
            } else {
                o << static_cast<char>(c);
            }
        }
    }
    return o.str();
}

void event(const std::string& name, const std::string& fields = "") {
    std::cout << "{\"event\":\"" << json_escape(name) << "\"";
    if (!fields.empty()) std::cout << "," << fields;
    std::cout << "}\n";
    std::cout.flush();
}

[[noreturn]] void final_exit(bool ok, int exit_code, int plugin_rc, int stage, int code, const std::string& msg) {
    std::cout << "{\"event\":\"result\",\"ok\":" << (ok ? "true" : "false")
              << ",\"plugin_rc\":" << plugin_rc
              << ",\"stage\":" << stage
              << ",\"code\":" << code
              << ",\"msg\":\"" << json_escape(msg) << "\"}\n";
    std::cout.flush();
#ifdef _WIN32
    // ExitProcess runs DLL_PROCESS_DETACH handlers. The stock Bambu network
    // plugin still owns worker threads at this point and v2.4 could fast-fail
    // with 0xC0000409 *after* it had already reported PrintingStageFinished.
    // This bridge is intentionally a one-shot child process, so terminate it
    // without running plugin detach/destructors after stdout has been flushed.
    TerminateProcess(GetCurrentProcess(), static_cast<UINT>(exit_code));
    for (;;) { Sleep(INFINITE); }
#else
    std::_Exit(exit_code);
#endif
}

struct Args {
    bool probe = false;
    std::string plugin;
    std::string data_dir;
    std::string cert_file;
    std::string gcode;
    std::string dev_id;
    std::string dev_ip;
    bool use_ams = false;
    std::string ams_mapping;
    std::string ams_mapping2;
    std::string ams_mapping_info;
    int timeout_seconds = 120;
};

bool parse_bool(const std::string& v) {
    return v == "1" || v == "true" || v == "TRUE" || v == "yes";
}

Args parse_args(int argc, char** argv) {
    Args a;
    for (int i = 1; i < argc; ++i) {
        std::string key = argv[i];
        if (key == "--probe") { a.probe = true; continue; }
        if (i + 1 >= argc) throw std::runtime_error("missing value for " + key);
        std::string value = argv[++i];
        if (key == "--plugin") a.plugin = value;
        else if (key == "--data-dir") a.data_dir = value;
        else if (key == "--cert-file") a.cert_file = value;
        else if (key == "--gcode") a.gcode = value;
        else if (key == "--dev-id") a.dev_id = value;
        else if (key == "--dev-ip") a.dev_ip = value;
        else if (key == "--use-ams") a.use_ams = parse_bool(value);
        else if (key == "--ams-mapping") a.ams_mapping = value;
        else if (key == "--ams-mapping2") a.ams_mapping2 = value;
        else if (key == "--ams-mapping-info") a.ams_mapping_info = value;
        else if (key == "--timeout") a.timeout_seconds = (std::max)(10, std::stoi(value));
        else throw std::runtime_error("unknown argument: " + key);
    }
    if (a.plugin.empty()) throw std::runtime_error("--plugin is required");
    if (!a.probe) {
        if (a.data_dir.empty() || a.cert_file.empty() || a.gcode.empty() || a.dev_id.empty() || a.dev_ip.empty())
            throw std::runtime_error("print mode requires --data-dir --cert-file --gcode --dev-id --dev-ip");
        if (a.ams_mapping.empty() || a.ams_mapping2.empty() || a.ams_mapping_info.empty())
            throw std::runtime_error("print mode requires complete AMS/source mapping strings");
    }
    return a;
}

#ifdef _WIN32
std::wstring wide_from_utf8(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(), static_cast<int>(s.size()), nullptr, 0);
    if (n <= 0) {
        n = MultiByteToWideChar(CP_ACP, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
        if (n <= 0) throw std::runtime_error("path conversion failed");
        std::wstring w(static_cast<size_t>(n), L'\0');
        MultiByteToWideChar(CP_ACP, 0, s.data(), static_cast<int>(s.size()), w.data(), n);
        return w;
    }
    std::wstring w(static_cast<size_t>(n), L'\0');
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(), static_cast<int>(s.size()), w.data(), n);
    return w;
}

class Module {
public:
    explicit Module(const std::string& path) {
        fs::path p(path);
        auto dir = p.parent_path().wstring();
        SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS | LOAD_LIBRARY_SEARCH_USER_DIRS);
        cookie_ = AddDllDirectory(dir.c_str());
        module_ = LoadLibraryExW(
            p.wstring().c_str(), nullptr,
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS | LOAD_LIBRARY_SEARCH_USER_DIRS
        );
        if (!module_) {
            throw std::runtime_error("LoadLibraryExW failed: " + std::to_string(GetLastError()));
        }
    }
    ~Module() {
        if (module_) FreeLibrary(module_);
        if (cookie_) RemoveDllDirectory(cookie_);
    }
    template <class T>
    T require(const char* name) const {
        auto p = GetProcAddress(module_, name);
        if (!p) throw std::runtime_error(std::string("missing export: ") + name);
        return reinterpret_cast<T>(p);
    }
    template <class T>
    T optional(const char* name) const {
        auto p = GetProcAddress(module_, name);
        return reinterpret_cast<T>(p);
    }
private:
    HMODULE module_ = nullptr;
    DLL_DIRECTORY_COOKIE cookie_ = nullptr;
};
#endif

struct ReadyLatch {
    std::mutex m;
    std::condition_variable cv;
    bool ready = false;
    void signal() { { std::lock_guard<std::mutex> lk(m); ready = true; } cv.notify_all(); }
    bool wait_for(std::chrono::milliseconds d) {
        std::unique_lock<std::mutex> lk(m);
        return cv.wait_for(lk, d, [&]{ return ready; });
    }
};

struct FinishLatch {
    std::mutex m;
    std::condition_variable cv;
    bool done = false;
    int stage = -1;
    int code = 0;
    std::string msg;
    void update(int s, int c, std::string text) {
        event("print_progress", "\"stage\":" + std::to_string(s) + ",\"code\":" + std::to_string(c) + ",\"msg\":\"" + json_escape(text) + "\"");
        if (s == BBL::PrintingStageFinished || s == BBL::PrintingStageERROR) {
            { std::lock_guard<std::mutex> lk(m); stage = s; code = c; msg = std::move(text); done = true; }
            cv.notify_all();
        }
    }
    bool wait_for(std::chrono::seconds d) {
        std::unique_lock<std::mutex> lk(m);
        return cv.wait_for(lk, d, [&]{ return done; });
    }
};

std::pair<std::string, std::string> split_cert(const std::string& cert_file) {
    fs::path p(cert_file);
    return {p.parent_path().string(), p.filename().string()};
}

std::string basename_utf8(const std::string& path) {
    return fs::path(path).filename().string();
}

} // namespace

int main(int argc, char** argv) {
#ifndef _WIN32
    (void)argc; (void)argv;
    std::cerr << "Windows only\n";
    return 70;
#else
    try {
        Args args = parse_args(argc, argv);
        Module mod(args.plugin);

        auto get_version = mod.require<func_get_version>("bambu_network_get_version");
        std::string plugin_version = get_version();
        event("plugin_loaded", "\"version\":\"" + json_escape(plugin_version) + "\"");
        if (plugin_version.rfind("02.07.01.", 0) != 0) {
            throw std::runtime_error("plugin ABI prefix is not 02.07.01.x: " + plugin_version);
        }
        if (args.probe) {
            // Resolve every symbol used by print mode without creating an agent.
            mod.require<func_create_agent>("bambu_network_create_agent");
            mod.require<func_destroy_agent>("bambu_network_destroy_agent");
            mod.require<func_set_config_dir>("bambu_network_set_config_dir");
            mod.require<func_init_log>("bambu_network_init_log");
            mod.require<func_set_cert_file>("bambu_network_set_cert_file");
            mod.require<func_set_country_code>("bambu_network_set_country_code");
            mod.require<func_start>("bambu_network_start");
            mod.require<func_set_on_local_connect_fn>("bambu_network_set_on_local_connect_fn");
            mod.require<func_set_on_local_message_fn>("bambu_network_set_on_local_message_fn");
            mod.require<func_set_on_printer_connected_fn>("bambu_network_set_on_printer_connected_fn");
            mod.require<func_bind_detect>("bambu_network_bind_detect");
            mod.require<func_connect_printer>("bambu_network_connect_printer");
            mod.require<func_start_subscribe>("bambu_network_start_subscribe");
            mod.require<func_send_message_to_printer>("bambu_network_send_message_to_printer");
            mod.require<func_install_device_cert>("bambu_network_install_device_cert");
            mod.require<func_start_local_print>("bambu_network_start_local_print");
            final_exit(true, 0, 0, BBL::PrintingStageFinished, 0, "probe_ok");
        }

        const char* secret_env = std::getenv("BAMBU_NATIVE_ACCESS_CODE");
        std::string access_code = secret_env ? secret_env : "";
        if (access_code.empty()) throw std::runtime_error("BAMBU_NATIVE_ACCESS_CODE is empty");

        auto create_agent = mod.require<func_create_agent>("bambu_network_create_agent");
        auto destroy_agent = mod.require<func_destroy_agent>("bambu_network_destroy_agent");
        auto init_log = mod.require<func_init_log>("bambu_network_init_log");
        auto set_config_dir = mod.require<func_set_config_dir>("bambu_network_set_config_dir");
        auto set_cert_file = mod.require<func_set_cert_file>("bambu_network_set_cert_file");
        auto set_country_code = mod.require<func_set_country_code>("bambu_network_set_country_code");
        auto start_agent = mod.require<func_start>("bambu_network_start");
        auto set_local_connect = mod.require<func_set_on_local_connect_fn>("bambu_network_set_on_local_connect_fn");
        auto set_local_message = mod.require<func_set_on_local_message_fn>("bambu_network_set_on_local_message_fn");
        auto set_printer_connected = mod.require<func_set_on_printer_connected_fn>("bambu_network_set_on_printer_connected_fn");
        auto bind_detect = mod.require<func_bind_detect>("bambu_network_bind_detect");
        auto connect_printer = mod.require<func_connect_printer>("bambu_network_connect_printer");
        auto disconnect_printer = mod.require<func_disconnect_printer>("bambu_network_disconnect_printer");
        auto start_subscribe = mod.require<func_start_subscribe>("bambu_network_start_subscribe");
        auto send_to_printer = mod.require<func_send_message_to_printer>("bambu_network_send_message_to_printer");
        auto install_device_cert = mod.require<func_install_device_cert>("bambu_network_install_device_cert");
        auto start_local_print = mod.require<func_start_local_print>("bambu_network_start_local_print");
        auto change_user = mod.optional<func_change_user>("bambu_network_change_user");
        auto enable_multi_machine = mod.optional<func_enable_multi_machine>("bambu_network_enable_multi_machine");
        auto start_discovery = mod.optional<func_start_discovery>("bambu_network_start_discovery");
        auto set_queue_on_main = mod.optional<func_set_queue_on_main_fn>("bambu_network_set_queue_on_main_fn");
        auto set_get_country = mod.optional<func_set_get_country_code_fn>("bambu_network_set_get_country_code_fn");
        auto set_extra_headers = mod.optional<func_set_extra_http_header>("bambu_network_set_extra_http_header");
        auto set_message = mod.optional<func_set_on_message_fn>("bambu_network_set_on_message_fn");
        auto set_user_message = mod.optional<func_set_on_user_message_fn>("bambu_network_set_on_user_message_fn");
        auto set_http_error = mod.optional<func_set_on_http_error_fn>("bambu_network_set_on_http_error_fn");
        auto set_sub_failure = mod.optional<func_set_on_subscribe_failure_fn>("bambu_network_set_on_subscribe_failure_fn");
        auto set_ssdp = mod.optional<func_set_on_ssdp_msg_fn>("bambu_network_set_on_ssdp_msg_fn");
        auto set_server_callback = mod.optional<func_set_server_callback>("bambu_network_set_server_callback");

        void* agent = create_agent(args.data_dir);
        if (!agent) throw std::runtime_error("bambu_network_create_agent returned null");

        ReadyLatch session;
        FinishLatch finish;
        std::atomic<bool> connected{false};

        set_local_message(agent, [](std::string dev, std::string msg) {
            event("local_message", "\"dev_id\":\"" + json_escape(dev) + "\",\"bytes\":" + std::to_string(msg.size()));
        });
        if (set_message) set_message(agent, [](std::string, std::string) {});
        if (set_user_message) set_user_message(agent, [](std::string, std::string) {});
        set_local_connect(agent, [&session, &connected](int status, std::string dev, std::string msg) {
            event("local_connect", "\"status\":" + std::to_string(status) + ",\"dev_id\":\"" + json_escape(dev) + "\",\"msg\":\"" + json_escape(msg) + "\"");
            if (status == BBL::ConnectStatusOk) { connected.store(true); session.signal(); }
        });
        set_printer_connected(agent, [&session, &connected](std::string topic) {
            event("printer_connected", "\"topic\":\"" + json_escape(topic) + "\"");
            connected.store(true); session.signal();
        });
        if (set_http_error) set_http_error(agent, [](unsigned code, std::string body) {
            event("http_error", "\"code\":" + std::to_string(code) + ",\"bytes\":" + std::to_string(body.size()));
        });
        if (set_sub_failure) set_sub_failure(agent, [](std::string topic) {
            event("subscribe_failure", "\"topic\":\"" + json_escape(topic) + "\"");
        });
        if (set_get_country) set_get_country(agent, [] { return std::string("US"); });
        if (set_queue_on_main) set_queue_on_main(agent, [](std::function<void()> fn) { if (fn) fn(); });
        if (set_server_callback) set_server_callback(agent, [](std::string, int) {});
        if (set_ssdp) set_ssdp(agent, [](std::string) {});

        auto cert_parts = split_cert(args.cert_file);
        set_config_dir(agent, args.data_dir);
        init_log(agent);
        set_cert_file(agent, cert_parts.first, cert_parts.second);
        if (set_extra_headers) {
            std::map<std::string, std::string> headers;
            headers["X-BBL-Client-Type"] = "slicer";
            headers["X-BBL-Client-Name"] = "BambuStudio";
            headers["X-BBL-Client-Version"] = "02.07.01.62";
            headers["X-BBL-OS-Type"] = "windows";
            headers["X-BBL-OS-Version"] = "10";
            headers["X-BBL-Language"] = "zh_CN";
            set_extra_headers(agent, headers);
        }
        set_country_code(agent, "US");
        int rc_start = start_agent(agent);
        event("agent_start", "\"rc\":" + std::to_string(rc_start));
        if (enable_multi_machine) enable_multi_machine(agent, false);
        if (start_discovery) start_discovery(agent, true, false);
        if (change_user) change_user(agent, "");

        BBL::detectResult detect{};
        int rc_detect = bind_detect(agent, args.dev_ip, "secure", detect);
        event("bind_detect", "\"rc\":" + std::to_string(rc_detect) + ",\"dev_id\":\"" + json_escape(detect.dev_id) + "\",\"connect_type\":\"" + json_escape(detect.connect_type) + "\"");

        int rc_conn = connect_printer(agent, args.dev_id, args.dev_ip, "bblp", access_code, true);
        access_code.clear();
        event("connect_printer", "\"rc\":" + std::to_string(rc_conn));
        if (rc_conn != 0) final_exit(false, 71, rc_conn, BBL::PrintingStageERROR, rc_conn, "connect_printer failed");
        if (!session.wait_for(20s)) final_exit(false, 72, rc_conn, BBL::PrintingStageERROR, -1, "LAN session did not become ready");

        int rc_sub = start_subscribe(agent, "app");
        event("start_subscribe", "\"rc\":" + std::to_string(rc_sub));
        const std::string pushall = "{\"pushing\":{\"sequence_id\":\"0\",\"command\":\"pushall\",\"version\":1,\"push_target\":1}}";
        int rc_push = send_to_printer(agent, args.dev_id, pushall, 1, 0);
        event("pushall", "\"rc\":" + std::to_string(rc_push));
        if (rc_push != 0) final_exit(false, 73, rc_push, BBL::PrintingStageERROR, rc_push, "pushall publish failed");
        send_to_printer(agent, args.dev_id, "{\"info\":{\"sequence_id\":\"1\",\"command\":\"get_version\"}}", 1, 0);
        send_to_printer(agent, args.dev_id, "{\"system\":{\"sequence_id\":\"2\",\"command\":\"get_access_code\"}}", 1, 0);

        // Reuse the real Studio data directory/cert state; LAN-mode provisioning is
        // enough for the user's current Developer Mode setup and never starts GUI.
        install_device_cert(agent, args.dev_id, true);
        std::this_thread::sleep_for(3500ms);

        BBL::PrintParams p{};
        p.dev_id = args.dev_id;
        p.task_name = basename_utf8(args.gcode);
        p.project_name = basename_utf8(args.gcode);
        p.preset_name = "plate_1";
        p.filename = args.gcode;
        p.config_filename = "";
        p.plate_index = 1;
        p.ftp_folder = "";
        p.ftp_file = basename_utf8(args.gcode);
        p.ftp_file_md5 = "";
        p.nozzle_mapping = "";
        p.ams_mapping = args.ams_mapping;
        p.ams_mapping2 = args.ams_mapping2;
        p.ams_mapping_info = args.ams_mapping_info;
        p.nozzles_info = "[]";
        p.connection_type = "lan";
        p.comments = "";
        p.origin_profile_id = 0;
        p.stl_design_id = 0;
        p.origin_model_id = "";
        p.print_type = "from_normal";
        p.dst_file = "";
        p.dev_name = "";
        p.dev_ip = args.dev_ip;
        p.use_ssl_for_ftp = true;
        p.use_ssl_for_mqtt = true;
        p.username = "bblp";
        const char* secret_again = std::getenv("BAMBU_NATIVE_ACCESS_CODE");
        p.password = secret_again ? secret_again : "";
        p.task_bed_leveling = true;
        p.task_flow_cali = true;
        p.task_vibration_cali = true;
        p.task_layer_inspect = true;
        p.task_record_timelapse = false;
        p.task_timelapse_use_internal = false;
        p.task_use_ams = args.use_ams;
        p.task_bed_type = "textured_plate";
        p.extra_options = "";
        p.auto_bed_leveling = 0;
        p.auto_flow_cali = 0;
        p.auto_offset_cali = 0;
        p.extruder_cali_manual_mode = 0;
        p.task_ext_change_assist = false;
        p.try_emmc_print = true;
        p.svc_context = "";

        auto on_update = [&finish](int status, int code, std::string msg) {
            finish.update(status, code, std::move(msg));
        };
        auto on_cancel = []() { return false; };

        event(
            "dispatch_params",
            "\"use_ams\":" + std::string(args.use_ams ? "true" : "false")
            + ",\"ams_mapping\":\"" + json_escape(args.ams_mapping) + "\""
            + ",\"ams_mapping2\":\"" + json_escape(args.ams_mapping2) + "\""
        );
        int rc_action = start_local_print(agent, p, on_update, on_cancel);
        p.password.clear();
        event("action_dispatched", "\"rc\":" + std::to_string(rc_action) + ",\"use_ams\":" + std::string(args.use_ams ? "true" : "false"));
        if (rc_action != 0 && !finish.done) {
            final_exit(false, 74, rc_action, BBL::PrintingStageERROR, rc_action, "start_local_print returned failure");
        }
        if (!finish.wait_for(std::chrono::seconds(args.timeout_seconds))) {
            final_exit(false, 75, rc_action, -1, -1, "timed out waiting for stock plugin print completion callback");
        }
        bool ok = finish.stage == BBL::PrintingStageFinished && finish.code == 0;
        // Dedicated bridge process: emit the result then let Windows reap the plugin's
        // worker threads. Calling destroy_agent synchronously can block ~60s in stock builds.
        final_exit(ok, ok ? 0 : 76, rc_action, finish.stage, finish.code, finish.msg);
    } catch (const std::exception& e) {
        final_exit(false, 70, -1, BBL::PrintingStageERROR, -1, e.what());
    } catch (...) {
        final_exit(false, 70, -1, BBL::PrintingStageERROR, -1, "unknown native bridge failure");
    }
#endif
}
