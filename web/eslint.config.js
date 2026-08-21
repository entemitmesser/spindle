import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import eslintConfigPrettier from "eslint-config-prettier";
import globals from "globals";
import { defineConfig, globalIgnores } from "eslint/config";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores(["dist", "src/routeTree.gen.ts", "src/lib/api-types.ts"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
      eslintConfigPrettier,
    ],
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
    },
  },
  {
    // Route modules (TanStack Router) and shadcn ui primitives legitimately
    // co-export a hook/variant helper alongside their component; so do the
    // app's context providers (`useTheme` beside `ThemeProvider`, etc).
    files: ["src/routes/**/*.tsx", "src/components/ui/**/*.tsx", "src/providers/**/*.tsx"],
    rules: {
      "react-refresh/only-export-components": "off",
    },
  },
]);
