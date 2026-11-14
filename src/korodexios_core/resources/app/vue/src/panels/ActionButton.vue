<script setup lang="ts">
import { computed, ref } from "vue";

import { argvFrom, runCommand } from "../kdx";
import type { ButtonSpec } from "../kdx";
import { store, t } from "../state";

const props = defineProps<{ button: ButtonSpec }>();
const armed = ref(false);
const output = ref("");
const command = computed(() => store.commands.find((item) => item.path === props.button.command));

// A button that confirms (or is dangerous) needs a second click within five seconds.
async function click() {
  if ((props.button.confirm || props.button.danger) && !armed.value) {
    armed.value = true;
    window.setTimeout(() => (armed.value = false), 5000);
    return;
  }
  armed.value = false;
  if (!command.value) return;
  const result = await runCommand(command.value.path, argvFrom(command.value, props.button.args), (text) => (output.value = text));
  output.value = result.output;
}
</script>

<template>
  <div>
    <button type="button" :class="{ danger: button.danger }" :disabled="!command" @click="click">
      {{ armed ? t("Click again to run") : button.label }}
    </button>
    <pre v-if="output" class="terminal">{{ output }}</pre>
  </div>
</template>
