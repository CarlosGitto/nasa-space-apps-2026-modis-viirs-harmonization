// Lint: correctness (Vue), translations (vue-i18n) and accessibility (vuejs-accessibility).
// The i18n rules stop text written straight into a template and keys missing from (or
// left unused in) src/locales; the a11y rules stop things like a clickable <div> without
// keyboard support or an input without a label. Run: npm run lint (or make frontend-lint).
import js from '@eslint/js'
import vueI18n from '@intlify/eslint-plugin-vue-i18n'
import pluginVue from 'eslint-plugin-vue'
import vueA11y from 'eslint-plugin-vuejs-accessibility'
import globals from 'globals'

export default [
  { ignores: ['dist/**', 'node_modules/**'] },
  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],
  ...vueA11y.configs['flat/recommended'],
  ...vueI18n.configs['flat/recommended'],
  {
    languageOptions: { ecmaVersion: 'latest', sourceType: 'module', globals: globals.browser },
    settings: {
      'vue-i18n': { localeDir: './src/locales/*.json', messageSyntaxVersion: '^11.0.0' },
    },
    rules: {
      // Symbols, numbers and technical names (sensor names, language codes) are not translated.
      '@intlify/vue-i18n/no-raw-text': ['error', {
        ignorePattern: '^[\\s\\-–—·:/%|()+×▶◀▾▸▼▲❚✕▱⚠*.,0-9]*$',
        ignoreText: ['MODIS', 'VIIRS', 'ES', 'EN'],
      }],
      '@intlify/vue-i18n/no-missing-keys': 'error',
      // Keys built on purpose from a value (`panel.tabs.${id}`) can't be seen statically:
      // their families are listed here. Every other key must be used.
      '@intlify/vue-i18n/no-unused-keys': ['error', {
        extensions: ['.js', '.vue'],
        ignores: [
          '/^panel\\.tabs\\./', '/^obs\\.cols\\./', '/^controls\\.(products|sensors)\\./', '/^gibs\\./',
          '/^errors\\./', '/^a11y\\.(normal|large|xlarge)$/', '/^curtain\\.(fire|trueColor|mapOnly)$/',
        ],
      }],
      '@intlify/vue-i18n/no-dynamic-keys': 'off', // keys like `panel.tabs.${id}` are deliberate
      '@intlify/vue-i18n/no-v-html': 'error',
      'vue/multi-word-component-names': 'off', // existing single-word names (Controls...)
      // A <label> wrapping its control is enough (the rule's default also wants id/for).
      'vuejs-accessibility/label-has-for': ['error', { required: { some: ['nesting', 'id'] } }],
    },
  },
  {
    files: ['vite.config.js', 'eslint.config.js'],
    languageOptions: { globals: globals.node },
  },
]
