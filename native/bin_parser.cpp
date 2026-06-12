/*
 * BIN Parser DLL - Fast BIN texture path extraction for Blender addon
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>
#include <unordered_map>
#include <fstream>
#include <sstream>

#include "../ritobin-master/ritobin_lib/src/ritobin/bin_types.hpp"
#include "../ritobin-master/ritobin_lib/src/ritobin/bin_types_helper.hpp"
#include "../ritobin-master/ritobin_lib/src/ritobin/bin_io.hpp"

namespace ritobin::io {
    struct BinCompatDefault : BinCompat {
        char const* name() const noexcept override { return "default"; }
        bool type_to_raw(Type type, uint8_t &raw) const noexcept override {
            raw = static_cast<uint8_t>(type);
            return true;
        }
        bool raw_to_type(uint8_t raw, Type &type) const noexcept override {
            type = static_cast<Type>(raw);
            if (ValueHelper::is_primitive(type)) {
                return type <= ValueHelper::MAX_PRIMITIVE;
            } else {
                return type <= ValueHelper::MAX_COMPLEX;
            }
        }
    };
    static BinCompatDefault g_compat_default;
}

#ifdef BUILD_DLL
    #define DLL_EXPORT extern "C" __declspec(dllexport)
#else
    #define DLL_EXPORT extern "C" __declspec(dllimport)
#endif

// Hash constants for BIN field lookup
static const uint32_t HASH_SKIN_MESH_PROPERTIES = 0x45ff5904;
static const uint32_t HASH_TEXTURE = 0x3c6468f4;
static const uint32_t HASH_MATERIAL_OVERRIDE = 0x24725910;
static const uint32_t HASH_SUBMESH = 0xaad7612c;
static const uint32_t HASH_MATERIAL_LINK = 0xd2e4d060;
static const uint32_t HASH_SAMPLER_VALUES = 0x0a6f0eb5;
static const uint32_t HASH_PROP_NAME = 0xb311d4ef;
static const uint32_t HASH_PROP_VALUE = 0xf0a363e3;

static std::string get_string(const ritobin::Value& val) {
    if (auto* s = std::get_if<ritobin::String>(&val)) {
        return s->value;
    }
    return "";
}

static uint32_t get_link(const ritobin::Value& val) {
    if (auto* l = std::get_if<ritobin::Link>(&val)) {
        return l->value.hash();
    }
    return 0;
}

static const ritobin::Embed* get_embed(const ritobin::Value& val) {
    return std::get_if<ritobin::Embed>(&val);
}

static const ritobin::List* get_list(const ritobin::Value& val) {
    return std::get_if<ritobin::List>(&val);
}

static const ritobin::List2* get_list2(const ritobin::Value& val) {
    return std::get_if<ritobin::List2>(&val);
}

static const ritobin::Field* find_field(const ritobin::Embed& embed, uint32_t hash) {
    for (const auto& field : embed.items) {
        if (field.key.hash() == hash) {
            return &field;
        }
    }
    return nullptr;
}

static std::string extract_textures(const ritobin::Bin& bin) {
    std::unordered_map<std::string, std::string> results;
    std::unordered_map<uint32_t, const ritobin::Embed*> entries_map;

    auto entries_it = bin.sections.find("entries");
    if (entries_it == bin.sections.end()) return "";

    const auto* entries_map_val = std::get_if<ritobin::Map>(&entries_it->second);
    if (!entries_map_val) return "";

    // Build hash->entry map
    for (const auto& pair : entries_map_val->items) {
        if (auto* key_hash = std::get_if<ritobin::Hash>(&pair.key)) {
            if (auto* entry = std::get_if<ritobin::Embed>(&pair.value)) {
                entries_map[key_hash->value.hash()] = entry;
            }
        }
    }

    // Find skinMeshProperties entries
    for (const auto& pair : entries_map_val->items) {
        const auto* entry = get_embed(pair.value);
        if (!entry) continue;

        const auto* smp_field = find_field(*entry, HASH_SKIN_MESH_PROPERTIES);
        if (!smp_field) continue;

        const auto* skin_mesh = get_embed(smp_field->value);
        if (!skin_mesh) continue;

        // Get BASE texture
        if (auto* tex_field = find_field(*skin_mesh, HASH_TEXTURE)) {
            std::string tex = get_string(tex_field->value);
            if (!tex.empty()) {
                results["BASE"] = tex;
            }
        }

        // Process material overrides
        const auto* mo_field = find_field(*skin_mesh, HASH_MATERIAL_OVERRIDE);
        if (!mo_field) continue;

        const auto* override_list = get_list(mo_field->value);
        if (!override_list) continue;

        for (const auto& override_item : override_list->items) {
            const auto* override_embed = get_embed(override_item.value);
            if (!override_embed) continue;

            std::string mat_name;
            std::string tex_path;
            uint32_t linked_mat_hash = 0;

            if (auto* name_field = find_field(*override_embed, HASH_SUBMESH)) {
                mat_name = get_string(name_field->value);
            }

            if (auto* tex_field = find_field(*override_embed, HASH_TEXTURE)) {
                tex_path = get_string(tex_field->value);
            }

            if (auto* mat_field = find_field(*override_embed, HASH_MATERIAL_LINK)) {
                linked_mat_hash = get_link(mat_field->value);
            }

            // Follow material link if no direct texture
            if (tex_path.empty() && linked_mat_hash != 0) {
                auto mat_it = entries_map.find(linked_mat_hash);
                if (mat_it != entries_map.end()) {
                    const ritobin::Embed* mat_entry = mat_it->second;

                    if (auto* props_field = find_field(*mat_entry, HASH_SAMPLER_VALUES)) {
                        auto process_prop = [&](const ritobin::Value& item_val) {
                            const auto* prop_embed = get_embed(item_val);
                            if (!prop_embed) return false;

                            std::string p_name, p_val;
                            if (auto* pn = find_field(*prop_embed, HASH_PROP_NAME)) {
                                p_name = get_string(pn->value);
                            }
                            if (auto* pv = find_field(*prop_embed, HASH_PROP_VALUE)) {
                                p_val = get_string(pv->value);
                            }

                            if (p_name == "Diffuse_Texture" && !p_val.empty()) {
                                tex_path = p_val;
                                return true;
                            }
                            return false;
                        };

                        // Try List, List2, then Map
                        if (auto* lst = get_list(props_field->value)) {
                            for (const auto& item : lst->items) {
                                if (process_prop(item.value)) break;
                            }
                        } else if (auto* lst2 = get_list2(props_field->value)) {
                            for (const auto& item : lst2->items) {
                                if (process_prop(item.value)) break;
                            }
                        }
                    }
                }
            }

            if (!mat_name.empty() && !tex_path.empty()) {
                results[mat_name] = tex_path;
            }
        }
    }

    std::ostringstream oss;
    for (const auto& [key, value] : results) {
        oss << key << "=" << value << "\n";
    }
    return oss.str();
}

// ---------------------------------------------------------------------------
// materials.bin extraction (map geometry): StaticMaterialDef entries -> JSON
// ---------------------------------------------------------------------------

// Field hashes (FNV-1a of lowercased names, verified against ritobin::FNV1a):
static const uint32_t HASH_STATIC_MATERIAL_DEF = 0xff9d3409; // "StaticMaterialDef"
static const uint32_t HASH_NAME = 0x8d39bde6;                // "name"
// samplerValues item fields: textureName = sampler semantic ("Diffuse_Texture"),
// texturePath = asset path. samplerName is the legacy semantic field.
static const uint32_t HASH_SAMPLER_NAME = 0x02e7fb4c;        // "samplerName"
// HASH_PROP_NAME (0xb311d4ef) is "textureName", HASH_PROP_VALUE (0xf0a363e3)
// is "texturePath" — reused from the skin extractor above.

static void json_escape(std::ostringstream& oss, const std::string& s) {
    for (char c : s) {
        switch (c) {
            case '"': oss << "\\\""; break;
            case '\\': oss << "\\\\"; break;
            case '\n': oss << "\\n"; break;
            case '\r': oss << "\\r"; break;
            case '\t': oss << "\\t"; break;
            default:
                if ((unsigned char)c < 0x20) {
                    char buf[8];
                    snprintf(buf, sizeof(buf), "\\u%04x", c);
                    oss << buf;
                } else {
                    oss << c;
                }
        }
    }
}

static std::string extract_materials(const ritobin::Bin& bin) {
    std::ostringstream oss;
    oss << "{\"materials\":[";
    bool first_mat = true;

    auto entries_it = bin.sections.find("entries");
    if (entries_it == bin.sections.end()) return "{\"materials\":[]}";
    const auto* entries_map = std::get_if<ritobin::Map>(&entries_it->second);
    if (!entries_map) return "{\"materials\":[]}";

    for (const auto& pair : entries_map->items) {
        const auto* entry = get_embed(pair.value);
        if (!entry || entry->name.hash() != HASH_STATIC_MATERIAL_DEF) continue;

        uint32_t key_hash = 0;
        if (auto* key = std::get_if<ritobin::Hash>(&pair.key)) {
            key_hash = key->value.hash();
        }

        std::string mat_name;
        if (auto* name_field = find_field(*entry, HASH_NAME)) {
            mat_name = get_string(name_field->value);
        }

        if (!first_mat) oss << ",";
        first_mat = false;
        char keybuf[16];
        snprintf(keybuf, sizeof(keybuf), "%08x", key_hash);
        oss << "{\"key\":\"" << keybuf << "\",\"name\":\"";
        json_escape(oss, mat_name);
        oss << "\",\"samplers\":[";

        bool first_sampler = true;
        if (auto* sv_field = find_field(*entry, HASH_SAMPLER_VALUES)) {
            auto emit_sampler = [&](const ritobin::Value& item_val) {
                const auto* sampler = get_embed(item_val);
                if (!sampler) return;

                std::string sem, path;
                if (auto* f = find_field(*sampler, HASH_PROP_NAME)) {       // textureName
                    sem = get_string(f->value);
                }
                if (sem.empty()) {
                    if (auto* f = find_field(*sampler, HASH_SAMPLER_NAME)) { // legacy
                        sem = get_string(f->value);
                    }
                }
                if (auto* f = find_field(*sampler, HASH_PROP_VALUE)) {      // texturePath
                    path = get_string(f->value);
                }
                if (path.empty()) return;

                if (!first_sampler) oss << ",";
                first_sampler = false;
                oss << "{\"name\":\"";
                json_escape(oss, sem);
                oss << "\",\"texture\":\"";
                json_escape(oss, path);
                oss << "\"}";
            };

            if (auto* lst = get_list(sv_field->value)) {
                for (const auto& item : lst->items) emit_sampler(item.value);
            } else if (auto* lst2 = get_list2(sv_field->value)) {
                for (const auto& item : lst2->items) emit_sampler(item.value);
            }
        }
        oss << "]}";
    }

    oss << "]}";
    return oss.str();
}

DLL_EXPORT int parse_materials_bin(const char* bin_path, uint8_t** out_data, uint32_t* out_size) {
    std::ifstream file(bin_path, std::ios::binary | std::ios::ate);
    if (!file.is_open()) return -1;

    std::streamsize size = file.tellg();
    file.seekg(0, std::ios::beg);

    std::vector<char> buffer(size);
    if (!file.read(buffer.data(), size)) return -1;
    file.close();

    ritobin::Bin bin;
    std::string error = ritobin::io::read_binary(bin, buffer, &ritobin::io::g_compat_default);
    if (!error.empty()) return -2;

    std::string result = extract_materials(bin);

    uint32_t result_size = (uint32_t)result.size();
    uint8_t* result_data = (uint8_t*)malloc(result_size + 1);
    if (!result_data) return -4;

    memcpy(result_data, result.c_str(), result_size + 1);
    *out_data = result_data;
    *out_size = result_size;
    return 0;
}

DLL_EXPORT int parse_bin_textures(const char* bin_path, uint8_t** out_data, uint32_t* out_size) {
    std::ifstream file(bin_path, std::ios::binary | std::ios::ate);
    if (!file.is_open()) return -1;

    std::streamsize size = file.tellg();
    file.seekg(0, std::ios::beg);

    std::vector<char> buffer(size);
    if (!file.read(buffer.data(), size)) return -1;
    file.close();

    ritobin::Bin bin;
    std::string error = ritobin::io::read_binary(bin, buffer, &ritobin::io::g_compat_default);
    if (!error.empty()) return -2;

    std::string result = extract_textures(bin);

    uint32_t result_size = (uint32_t)result.size();
    uint8_t* result_data = (uint8_t*)malloc(result_size + 1);
    if (!result_data) return -4;

    memcpy(result_data, result.c_str(), result_size + 1);
    *out_data = result_data;
    *out_size = result_size;
    return 0;
}

DLL_EXPORT void free_bin_result(uint8_t* data) {
    if (data) free(data);
}

DLL_EXPORT const char* get_bin_parser_version() {
    return "bin_parser 1.2";
}

BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID lpReserved) {
    (void)hinstDLL; (void)lpReserved;
    return TRUE;
}
