import {
  fauxProvider,
  fauxAssistantMessage,
} from "@earendil-works/pi-ai/providers/faux";
export default function (pi) {
  const faux = fauxProvider();
  faux.setResponses([fauxAssistantMessage("CLI smoke complete")]);
  pi.registerProvider(faux.provider);
}
