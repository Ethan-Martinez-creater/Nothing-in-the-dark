import eslint from '@eslint/js'
import eslintConfigPrettier from '@vue/eslint-config-prettier/skip-formatting'
import { vueTsConfigs, withVueTs } from '@vue/eslint-config-typescript'
import pluginVue from 'eslint-plugin-vue'

export default withVueTs(
  {
    ignores: ['**/dist/**', '**/node_modules/**', 'e2e-*.cjs'],
  },
  eslint.configs.recommended,
  pluginVue.configs['flat/essential'],
  vueTsConfigs.recommended,
  eslintConfigPrettier,
)
