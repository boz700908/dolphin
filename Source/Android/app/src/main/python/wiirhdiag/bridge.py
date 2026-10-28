'''The one place that reaches the Android side of the assist.'''

from java import jclass

# The Kotlin object owns the interpreter, the guest RAM addresses, the speech
# queue and the cue mixer. Its static methods are the whole contract here.
AssistRuntime = jclass('org.dolphinemu.dolphinemu.assist.AssistRuntime')
