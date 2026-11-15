// The glasses stream this many frames a second.
export const FPS = 2;
// The questions the backend takes for déjà vu: a copy of _ASKS in backend/app/glasses/dejavu.py,
// so change the two together.
export const RECALL =
  /deja vu|déjà vu|\b(know|remember|recogni[sz]e|seen|see) (this|that)( one| thing| place)?( before| already| from( somewhere| before)?)?\W*$/i;
