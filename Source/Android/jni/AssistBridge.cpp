// Copyright 2026 Dolphin Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Exposes the guest RAM mapping to the Android narration runtime.
//
// The Python narration code runs inside this same process, so it can read
// MEM1/MEM2 directly through pointers instead of copying 88 MiB per frame.
// Nothing here writes to guest memory: the exported functions only report the
// host address of a mapping that Core already owns, and the sizes it owns.
//
// The pointers are valid only while emulation is running. Callers must check
// the sizes first and must not cache an address across a core start/stop.

#include <jni.h>

#include "Common/CommonTypes.h"

#include "Core/Core.h"
#include "Core/HW/Memmap.h"
#include "Core/System.h"

namespace
{
// A null reference is returned whenever the core cannot currently be read, so
// that callers have exactly one failure test (null pointer or zero size).
Memory::MemoryManager* GetMemoryIfRunning()
{
  auto& system = Core::System::GetInstance();
  if (!Core::IsRunning(system))
    return nullptr;

  Memory::MemoryManager& memory = system.GetMemory();
  if (!memory.IsInitialized())
    return nullptr;

  return &memory;
}
}  // namespace

extern "C" {

// Returns the host pointer of MEM1, or zero when no mapping is active.
JNIEXPORT jlong JNICALL
Java_org_dolphinemu_dolphinemu_assist_AssistNative_GetMem1Address(JNIEnv* env, jclass clazz)
{
  Memory::MemoryManager* memory = GetMemoryIfRunning();
  if (memory == nullptr)
    return 0;

  return reinterpret_cast<jlong>(memory->GetRAM());
}

// Returns the host pointer of MEM2, or zero when no mapping is active.
JNIEXPORT jlong JNICALL
Java_org_dolphinemu_dolphinemu_assist_AssistNative_GetMem2Address(JNIEnv* env, jclass clazz)
{
  Memory::MemoryManager* memory = GetMemoryIfRunning();
  if (memory == nullptr)
    return 0;

  return reinterpret_cast<jlong>(memory->GetEXRAM());
}

// Returns the usable MEM1 byte count, or zero when the core is not running.
JNIEXPORT jlong JNICALL
Java_org_dolphinemu_dolphinemu_assist_AssistNative_GetMem1Size(JNIEnv* env, jclass clazz)
{
  Memory::MemoryManager* memory = GetMemoryIfRunning();
  if (memory == nullptr)
    return 0;

  return static_cast<jlong>(memory->GetRamSizeReal());
}

// Returns the usable MEM2 byte count, or zero when the core is not running.
JNIEXPORT jlong JNICALL
Java_org_dolphinemu_dolphinemu_assist_AssistNative_GetMem2Size(JNIEnv* env, jclass clazz)
{
  Memory::MemoryManager* memory = GetMemoryIfRunning();
  if (memory == nullptr)
    return 0;

  return static_cast<jlong>(memory->GetExRamSizeReal());
}

}  // extern "C"
