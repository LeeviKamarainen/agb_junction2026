import {useState} from 'react';
import {
  App as WearablesApp,
  Button,
  ButtonRail,
  Page,
  Panel,
  ScrollView,
  TextStyle,
  TextView,
} from '@wearables-ui-toolkit/mrbd';

// Demo change: a tiny "pinch counter" that shows how D-pad focus and
// pinch (Select) work on Meta Ray-Ban Display.
//   ← / →  move focus between the two buttons
//   Enter  = pinch, presses the focused button
function messageFor(count: number): string {
  if (count === 0) return 'Pinch "+1" to start counting.';
  if (count >= 10) return 'Ten pinches. You have mastered the pinch!';
  if (count >= 5) return 'Halfway to ten. Keep going.';
  return `You have pinched ${count} ${count === 1 ? 'time' : 'times'}.`;
}

export default function App() {
  const [count, setCount] = useState(0);

  return (
    <WearablesApp>
      <Page headerText="Pinch Counter" enableSystemBarInset={false}>
        <div className="action-page-shell">
          <ScrollView insetForHeader ariaLabel="Counter" tabIndex={0}>
            <Panel width="100%">
              <div className="content-inset counter">
                <TextView as="p" textStyle={TextStyle.NUMERAL1} aria-live="polite">
                  {count}
                </TextView>
                <TextView as="p" textStyle={TextStyle.BODY2}>
                  {messageFor(count)}
                </TextView>
              </div>
            </Panel>
          </ScrollView>
          <div className="action-dock">
            <ButtonRail>
              <Button title="+1" onClick={() => setCount(current => current + 1)} />
              <Button title="Reset" onClick={() => setCount(0)} />
            </ButtonRail>
          </div>
        </div>
      </Page>
    </WearablesApp>
  );
}
