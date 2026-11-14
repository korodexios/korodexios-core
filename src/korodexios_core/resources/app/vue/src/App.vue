<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import { ApiError, manifest as loadManifest, status as loadStatus } from "./kdx";
import type { Manifest, ModuleSpec, Status } from "./kdx";
import PanelCard from "./panels/PanelCard.vue";
import SignIn from "./SignIn.vue";
import { loadMessages, loadState, save, store, t } from "./state";

const locale = ref("");
const status = ref<Status | null>(null);
const manifest = ref<Manifest | null>(null);
const error = ref("");
const locked = ref(false);
const tab = ref("");
// a new key after signing in mounts every panel again, so each loads with the session
const session = ref(0);

function fail(reason: unknown) {
  if (reason instanceof ApiError && reason.status === 401) locked.value = true;
  else error.value = (reason as Error).message;
}

function loadManifestFor(code: string) {
  loadManifest(code).then((value) => (manifest.value = value)).catch(fail);
}

function load() {
  locked.value = false;
  error.value = "";
  void loadState();
  void loadMessages(locale.value);
  loadStatus().then((value) => (status.value = value)).catch(fail);
  loadManifestFor(locale.value);
}

onMounted(load);
watch(locale, (code) => {
  void loadMessages(code);
  loadManifestFor(code);
});

function signedIn() {
  session.value += 1;
  load();
}

// The manifest is the default; the person's saved labels, order and hiding overlay it.
const modules = computed<ModuleSpec[]>(() => {
  const { nav, panels } = store.prefs;
  return (manifest.value?.modules ?? [])
    .filter((entry) => !nav[entry.module]?.hidden)
    .map((entry) => ({
      ...entry,
      title: nav[entry.module]?.label ?? entry.title,
      order: nav[entry.module]?.order ?? entry.order,
      panels: entry.panels
        .filter((panel) => !panels[`${entry.module}.${panel.id}`]?.hidden)
        .map((panel, index) => ({
          ...panel,
          title: panels[`${entry.module}.${panel.id}`]?.label ?? panel.title,
          order: panels[`${entry.module}.${panel.id}`]?.order ?? index * 10,
        }))
        .sort((a, b) => a.order - b.order),
    }))
    .sort((a, b) => a.order - b.order || a.module.localeCompare(b.module));
});

const active = computed(
  () =>
    modules.value.find((entry) => entry.module === tab.value) ??
    modules.value.find((entry) => entry.module === store.prefs.settings.last_tab) ??
    modules.value[0],
);

function choose(module: string) {
  tab.value = module;
  void save({ settings: { last_tab: module } });
}
</script>

<template>
  <SignIn v-if="locked" @done="signedIn" />
  <div v-else :key="session" class="shell">
    <header class="topbar">
      <h1>@TITLE@</h1>
      <span v-if="status" class="pill" data-testid="state">
        {{ status.app.state }} - {{ status.modules.total }} modules
      </span>
      <select v-model="locale" aria-label="language">
        <option value="">auto</option>
        <option value="en">English</option>
        <option value="sk">Slovenčina</option>
      </select>
    </header>
    <p v-if="error" class="error">{{ error }}</p>
    <nav class="tabs" role="tablist">
      <button
        v-for="entry in modules"
        :key="entry.module"
        role="tab"
        :aria-selected="entry.module === active?.module"
        :class="{ active: entry.module === active?.module }"
        @click="choose(entry.module)"
      >
        {{ entry.title }}
      </button>
    </nav>
    <main>
      <template v-if="active">
        <PanelCard v-for="panel in active.panels" :key="panel.id" :module="active.module" :panel="panel" />
      </template>
      <p v-else class="muted">{{ t("No module declares a [ui] table yet. Add one to a kdx_module.toml.") }}</p>
      <p
        v-for="issue in manifest?.issues ?? []"
        :key="`${issue.module}.${issue.panel ?? ''}.${issue.message}`"
        class="error"
      >
        {{ issue.module }}{{ issue.panel ? `.${issue.panel}` : "" }}: {{ issue.message }}
      </p>
    </main>
  </div>
</template>
