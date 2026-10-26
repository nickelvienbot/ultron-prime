import nextVitals from "eslint-config-next/core-web-vitals";

/**
 * Flat config (ESLint 9).
 *
 * eslint-config-next 16 ships native flat arrays. Do NOT route them through
 * FlatCompat: @eslint/eslintrc then tries to validate a flat config as legacy
 * and dies on a circular reference — `TypeError: Converting circular structure
 * to JSON`, pointing at the react plugin. core-web-vitals already includes the
 * `next` and `next/typescript` rule groups, so one import covers all three.
 */
const config = [
  { ignores: [".next/**", "node_modules/**", "harvest/**"] },
  ...nextVitals,
  {
    rules: {
      // Craft invariant, enforced rather than remembered: a number with no
      // source is the failure mode this project exists to avoid.
      "no-restricted-syntax": [
        "warn",
        {
          selector:
            "JSXAttribute[name.name='style'] > JSXExpressionContainer > ObjectExpression > Property[key.name='borderRadius']",
          message:
            "Zero border-radius is the design identity. Hard edges only — see src/app/globals.css.",
        },
      ],
    },
  },
];

export default config;