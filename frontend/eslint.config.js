// Flat config: eslint recommended + svelte plugin. Svelte 5 runes are compiler
// globals, declared here so .svelte.js store modules don't trip no-undef.
import js from "@eslint/js";
import svelte from "eslint-plugin-svelte";
import globals from "globals";

const runes = {
  $state: "readonly",
  $derived: "readonly",
  $effect: "readonly",
  $props: "readonly",
  $bindable: "readonly",
};

export default [
  js.configs.recommended,
  ...svelte.configs["flat/recommended"],
  {
    languageOptions: {
      globals: { ...globals.browser, ...runes },
    },
    rules: {
      // F3 (INVARIANTS.md): plain Sets + reassignment is the locked pattern.
      "svelte/prefer-svelte-reactivity": "off",
    },
  },
];
