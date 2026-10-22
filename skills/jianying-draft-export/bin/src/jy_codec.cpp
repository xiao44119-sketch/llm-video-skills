#include <iostream>
#include <string>
#include <fstream>
#include <dlfcn.h>
#include <cstring>

typedef void (*EnableFn)(void*, bool);
typedef std::string (*DecryptFn)(void*, const std::string&, const std::string&, bool&);
typedef std::string (*EncryptFn)(void*, const std::string&);

static void* g_handle = nullptr;
static EnableFn g_enable = nullptr;
static DecryptFn g_decrypt = nullptr;
static EncryptFn g_encrypt = nullptr;

extern "C" {

bool init_codec(const char* dylib_path) {
    if (g_handle) return true;
    if (!dylib_path) {
        dylib_path = "/Applications/VideoFusion-macOS.app/Contents/Frameworks/libvideoeditor.dylib";
    }
    g_handle = dlopen(dylib_path, RTLD_LAZY);
    if (!g_handle) {
        std::cerr << "dlopen failed: " << dlerror() << std::endl;
        return false;
    }
    g_enable = (EnableFn)dlsym(g_handle, "_ZN4lvve12EncryptUtils6enableEb");
    g_decrypt = (DecryptFn)dlsym(g_handle, "_ZN4lvve12EncryptUtils7decryptERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEES9_Rb");
    g_encrypt = (EncryptFn)dlsym(g_handle, "_ZN4lvve12EncryptUtils7encryptERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE");

    if (g_enable) {
        g_enable(nullptr, true);
    }
    return (g_decrypt != nullptr && g_encrypt != nullptr);
}

// C-compatible functions for Python ctypes
char* decrypt_buffer(const char* in_buf, size_t in_len, size_t* out_len) {
    if (!init_codec(nullptr)) return nullptr;
    std::string input(in_buf, in_len);
    bool ok = false;
    std::string output = g_decrypt(nullptr, input, "{}", ok);
    if (!ok) return nullptr;
    *out_len = output.size();
    char* res = (char*)malloc(output.size() + 1);
    memcpy(res, output.data(), output.size());
    res[output.size()] = '\0';
    return res;
}

char* encrypt_buffer(const char* in_buf, size_t in_len, size_t* out_len) {
    if (!init_codec(nullptr)) return nullptr;
    std::string input(in_buf, in_len);
    std::string output = g_encrypt(nullptr, input);
    if (output.empty()) return nullptr;
    *out_len = output.size();
    char* res = (char*)malloc(output.size() + 1);
    memcpy(res, output.data(), output.size());
    res[output.size()] = '\0';
    return res;
}

void free_buffer(char* buf) {
    if (buf) free(buf);
}

bool decrypt_file(const char* in_path, const char* out_path) {
    std::ifstream in(in_path, std::ios::binary);
    if (!in) return false;
    std::string content((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    size_t out_len = 0;
    char* dec = decrypt_buffer(content.data(), content.size(), &out_len);
    if (!dec) return false;
    std::ofstream out(out_path, std::ios::binary);
    out.write(dec, out_len);
    free_buffer(dec);
    return true;
}

bool encrypt_file(const char* in_path, const char* out_path) {
    std::ifstream in(in_path, std::ios::binary);
    if (!in) return false;
    std::string content((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    size_t out_len = 0;
    char* enc = encrypt_buffer(content.data(), content.size(), &out_len);
    if (!enc) return false;
    std::ofstream out(out_path, std::ios::binary);
    out.write(enc, out_len);
    free_buffer(enc);
    return true;
}

} // extern "C"

int main(int argc, char* argv[]) {
    if (argc < 4) {
        std::cout << "Usage: jy_codec -d <in.json> <out.json>\n"
                  << "       jy_codec -e <in.json> <out.json>\n";
        return 1;
    }
    if (strcmp(argv[1], "-d") == 0) {
        if (decrypt_file(argv[2], argv[3])) {
            std::cout << "Successfully decrypted " << argv[2] << " -> " << argv[3] << std::endl;
            return 0;
        } else {
            std::cerr << "Failed to decrypt " << argv[2] << std::endl;
            return 1;
        }
    } else if (strcmp(argv[1], "-e") == 0) {
        if (encrypt_file(argv[2], argv[3])) {
            std::cout << "Successfully encrypted " << argv[2] << " -> " << argv[3] << std::endl;
            return 0;
        } else {
            std::cerr << "Failed to encrypt " << argv[2] << std::endl;
            return 1;
        }
    }
    return 1;
}
