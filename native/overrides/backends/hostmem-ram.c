/*
 * QEMU Host Memory Backend
 *
 * Copyright (C) 2013-2014 Red Hat Inc
 *
 * Authors:
 *   Igor Mammedov <imammedo@redhat.com>
 *
 * This work is licensed under the terms of the GNU GPL, version 2 or later.
 * See the COPYING file in the top-level directory.
 */

#include "qemu/osdep.h"
#include "system/hostmem.h"
#include "qapi/error.h"
#include "qemu/module.h"
#include "qom/object_interfaces.h"

static bool
ram_backend_memory_alloc(HostMemoryBackend *backend, Error **errp)
{
    g_autofree char *name = NULL;
    uint32_t ram_flags;

    if (!backend->size) {
        error_setg(errp, "can't create backend with size 0");
        return false;
    }

    name = host_memory_backend_get_name(backend);
    ram_flags = backend->share ? RAM_SHARED : RAM_PRIVATE;
    ram_flags |= backend->reserve ? 0 : RAM_NORESERVE;
    ram_flags |= backend->guest_memfd ? RAM_GUEST_MEMFD : 0;
    return memory_region_init_ram_flags_nomigrate(&backend->mr, OBJECT(backend),
                                                  name, backend->size,
                                                  ram_flags, errp);
}

static void
ram_backend_class_init(ObjectClass *oc, const void *data)
{
    HostMemoryBackendClass *bc = MEMORY_BACKEND_CLASS(oc);

    bc->alloc = ram_backend_memory_alloc;
}

static const TypeInfo ram_backend_info = {
    .name = TYPE_MEMORY_BACKEND_RAM,
    .parent = TYPE_MEMORY_BACKEND,
    .class_init = ram_backend_class_init,
};

/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifdef _WIN32
#include "qemu/rcu.h"
#include <winioctl.h>

/* The pinned upstream Windows build does not register memory-backend-file.
 * This local implementation is exclusively a new, ephemeral NTFS file with
 * shared-write mapping semantics (not a COW view and not a named mapping). */
typedef struct Auto9WindowsFileView {
    struct rcu_head rcu;
    HANDLE file;
    void *view;
} Auto9WindowsFileView;

typedef struct Auto9WindowsFileRam {
    HostMemoryBackend parent;
    char *path;
    Auto9WindowsFileView *mapping;
} Auto9WindowsFileRam;

static void auto9_windows_view_release(Auto9WindowsFileView *map)
{
    if (map->view) {
        UnmapViewOfFile(map->view);
    }
    if (map->file != INVALID_HANDLE_VALUE) {
        CloseHandle(map->file);
    }
    g_free(map);
}

static bool auto9_windows_file_ram_alloc(HostMemoryBackend *backend,
                                         Error **errp)
{
    Auto9WindowsFileRam *s = (Auto9WindowsFileRam *)backend;
    g_autofree char *name = NULL;
    g_autofree gunichar2 *path = NULL;
    HANDLE section = NULL;
    LARGE_INTEGER end;
    FILE_ZERO_DATA_INFORMATION zero = { 0 };
    SYSTEM_INFO system;
    DWORD ignored;
    DWORD failure;

    GetSystemInfo(&system);
    if (!s->path || !g_path_is_absolute(s->path) || !backend->size ||
        backend->size > (uint64_t)G_MAXINT64 || backend->size > SIZE_MAX ||
        backend->size % system.dwAllocationGranularity ||
        !backend->share || backend->prealloc || backend->guest_memfd) {
        error_setg(errp, "Windows file RAM requires a new absolute file, "
                   "allocation-granularity size, share=on and prealloc=off");
        return false;
    }
    path = g_utf8_to_utf16(s->path, -1, NULL, NULL, NULL);
    if (!path) {
        error_setg(errp, "Windows file RAM path is not valid UTF-8");
        return false;
    }

    Auto9WindowsFileView *map = g_new0(Auto9WindowsFileView, 1);
    map->file = CreateFileW((const wchar_t *)path, GENERIC_READ | GENERIC_WRITE |
                            DELETE, 0, NULL, CREATE_NEW,
                            FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_DELETE_ON_CLOSE,
                            NULL);
    if (map->file == INVALID_HANDLE_VALUE) {
        goto fail;
    }
    end.QuadPart = backend->size;
    zero.BeyondFinalZero = end;
    if (!DeviceIoControl(map->file, FSCTL_SET_SPARSE, NULL, 0, NULL, 0,
                         &ignored, NULL) ||
        !SetFilePointerEx(map->file, end, NULL, FILE_BEGIN) ||
        !SetEndOfFile(map->file) ||
        !DeviceIoControl(map->file, FSCTL_SET_ZERO_DATA, &zero, sizeof(zero),
                         NULL, 0, &ignored, NULL)) {
        goto fail;
    }
    section = CreateFileMappingW(map->file, NULL, PAGE_READWRITE, 0, 0, NULL);
    if (!section) {
        goto fail;
    }
    map->view = MapViewOfFile(section, FILE_MAP_WRITE, 0, 0, backend->size);
    if (!map->view) {
        goto fail;
    }
    CloseHandle(section);
    name = host_memory_backend_get_name(backend);
    s->mapping = map;
    memory_region_init_ram_ptr(&backend->mr, OBJECT(backend), name,
                               backend->size, map->view);
    /* nvdimm_prepare_memory_region uses the backend's nonzero alignment. */
    backend->mr.align = system.dwAllocationGranularity;
    return true;

fail:
    failure = GetLastError();
    if (section) {
        CloseHandle(section);
    }
    auto9_windows_view_release(map);
    error_setg_win32(errp, failure, "Cannot create ephemeral Windows RAM '%s'",
                     s->path);
    return false;
}

static char *auto9_windows_file_ram_get_path(Object *obj, Error **errp)
{
    return g_strdup(((Auto9WindowsFileRam *)obj)->path);
}

static void auto9_windows_file_ram_set_path(Object *obj, const char *value,
                                            Error **errp)
{
    Auto9WindowsFileRam *s = (Auto9WindowsFileRam *)obj;
    if (s->mapping) {
        error_setg(errp, "Cannot change Windows RAM backing after allocation");
        return;
    }
    g_free(s->path);
    s->path = g_strdup(value);
}

static void auto9_windows_file_ram_finalize(Object *obj)
{
    Auto9WindowsFileRam *s = (Auto9WindowsFileRam *)obj;
    /* The RAMBlock wraps a PREALLOC pointer. Its release does not unmap it. */
    if (s->mapping) {
        call_rcu(s->mapping, auto9_windows_view_release, rcu);
    }
    g_free(s->path);
}

static void auto9_windows_file_ram_class(ObjectClass *oc, const void *data)
{
    MEMORY_BACKEND_CLASS(oc)->alloc = auto9_windows_file_ram_alloc;
    object_class_property_add_str(oc, "mem-path",
                                  auto9_windows_file_ram_get_path,
                                  auto9_windows_file_ram_set_path);
}

static const TypeInfo auto9_windows_file_ram_type = {
    .name = TYPE_MEMORY_BACKEND_FILE,
    .parent = TYPE_MEMORY_BACKEND,
    .instance_size = sizeof(Auto9WindowsFileRam),
    .instance_finalize = auto9_windows_file_ram_finalize,
    .class_init = auto9_windows_file_ram_class,
};
#endif

static void register_types(void)
{
    type_register_static(&ram_backend_info);
#ifdef _WIN32
    type_register_static(&auto9_windows_file_ram_type);
#endif
}

type_init(register_types);
