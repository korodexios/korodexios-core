import type { Component } from "vue";

import type { PanelType } from "../kdx";
import ActionsPanel from "./ActionsPanel.vue";
import EmbedPanel from "./EmbedPanel.vue";
import FormPanel from "./FormPanel.vue";
import KeyValuePanel from "./KeyValuePanel.vue";
import LogPanel from "./LogPanel.vue";
import SettingsPanel from "./SettingsPanel.vue";
import TablePanel from "./TablePanel.vue";
import TextPanel from "./TextPanel.vue";

// Every panel type has a drawing: a type added to the contract fails the type check here.
export const GENERIC: Record<PanelType, Component> = {
  table: TablePanel,
  keyvalue: KeyValuePanel,
  log: LogPanel,
  text: TextPanel,
  form: FormPanel,
  actions: ActionsPanel,
  embed: EmbedPanel,
  settings: SettingsPanel,
};
