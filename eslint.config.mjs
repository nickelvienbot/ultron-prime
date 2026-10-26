import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({ baseDirectory: __dirname });

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      // The craft invariants, enforced by lint rather than trusted to memory:
      // a number with no source is the failure mode this project exists to avoid.
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
  {
    ignores: [".next/**", "node_modules/**", "harvest/**"],
  },
];

export default eslintConfig;