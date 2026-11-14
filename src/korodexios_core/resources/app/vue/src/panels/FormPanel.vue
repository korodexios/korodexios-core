<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { argvFrom, runCommand } from "../kdx";
import type { PanelSpec, Scalar } from "../kdx";
import { save, store, t } from "../state";

const props = defineProps<{ module: string; panel: PanelSpec }>();
const key = computed(() => `${props.module}.${props.panel.id}`);
const command = computed(() => store.commands.find((item) => item.path === props.panel.command));
const values = ref<Record<string, Scalar>>({});
const output = ref("");

const defaults = () => ({ ...(props.panel.args ?? {}), ...(store.prefs.defaults[key.value] ?? {}) });

// Forms read the remembered values once, when the saved choices have arrived.
watch(
  () => store.ready,
  (ready) => {
    if (ready) values.value = { ...defaults(), ...(store.prefs.values[key.value] ?? {}) };
  },
  { immediate: true },
);

async function submit() {
  if (!command.value) return;
  await save({ values: { [key.value]: values.value } });
  const result = await runCommand(command.value.path, argvFrom(command.value, values.value), (text) => (output.value = text));
  output.value = result.output;
}
</script>

<template>
  <p v-if="store.ready && !command" class="muted">{{ panel.command }}</p>
  <form v-else-if="command" @submit.prevent="submit">
    <label v-for="arg in command.args" :key="arg.dest" class="field">
      <span>{{ arg.positional ? arg.dest : arg.flags[arg.flags.length - 1] }}</span>
      <input v-if="arg.kind === 'flag'" v-model="values[arg.dest]" type="checkbox" />
      <select v-else-if="arg.choices.length" v-model="values[arg.dest]">
        <option value="">(default)</option>
        <option v-for="choice in arg.choices" :key="choice">{{ choice }}</option>
      </select>
      <input
        v-else
        v-model="values[arg.dest]"
        :type="arg.kind === 'int' || arg.kind === 'float' ? 'number' : 'text'"
        :placeholder="arg.default != null ? `default: ${String(arg.default)}` : ''"
      />
      <small class="muted">{{ arg.help }}</small>
    </label>
    <div class="row">
      <button type="submit" class="primary">{{ t("Run") }}</button>
      <button type="button" @click="save({ defaults: { [key]: values } })">{{ t("Save as default") }}</button>
      <button type="button" @click="values = defaults()">{{ t("Restore defaults") }}</button>
    </div>
    <pre v-if="output" class="terminal">{{ output }}</pre>
  </form>
</template>
