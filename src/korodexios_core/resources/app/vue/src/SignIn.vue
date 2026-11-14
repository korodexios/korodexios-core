<script setup lang="ts">
import { ref } from "vue";

import { signIn } from "./kdx";
import { t } from "./state";

// Shown when the daemon refuses us: the token is typed here, never put into code or a URL.
const emit = defineEmits<{ done: [] }>();
const token = ref("");

async function submit() {
  await signIn(token.value);
  token.value = "";
  emit("done");
}
</script>

<template>
  <form class="card" @submit.prevent="submit">
    <h2>{{ t("Sign in") }}</h2>
    <p class="muted">{{ t("Paste the token from 'kdx ui --no-browser' or var/chassis.ipc.") }}</p>
    <input v-model="token" type="password" aria-label="token" autocomplete="off" />
    <button type="submit" class="primary">{{ t("Sign in") }}</button>
  </form>
</template>
