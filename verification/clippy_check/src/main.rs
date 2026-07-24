// Verification harness for Milestone 3 (ROADMAP.md #3): every function
// below was produced by `code-convert-helper convert`, taken verbatim from
// `output/sample.rs` (the `clamp`/`sum_up_to`/`greet`/`build_greeting`/
// `Counter` functions) plus a few extra snippets converted from small
// Python fragments covering ownership variants, collections, and
// precedence-sensitive expressions that don't fit in `examples/sample.py`.
// Run `cargo clippy --all-targets -- -D warnings` from this directory
// against a real Rust toolchain to confirm Milestone 3's "Done When"
// criterion (ROADMAP.md #3: "compiles and passes `cargo clippy` with
// zero warnings").
//
// `fetch_data` (the `requests.get(...)` example) is deliberately excluded
// here: it has unresolved TYPE HOLEs and an unconverted `requests` crate
// call, which is the *correct*, by-design outcome for stdlib calls
// outside the v1 core subset (see PROJECT_OVERVIEW.md) -- it's meant to
// fail loudly, not compile.

fn clamp(value: i64, lo: i64, hi: i64) -> i64 {
    // keep value within [lo, hi]
    if value < lo {
        return lo;
    }
    if value > hi {
        return hi;
    }
    value
}

fn sum_up_to(n: i64) -> i64 {
    let mut total: i64 = 0;
    for i in 0..n {
        total += i;
    }
    total
}

fn greet(name: &String) -> &String {
    // 'name' is only read here, so a borrow avoids an unnecessary clone;
    // the return type echoes that same reference automatically.
    name
}

fn build_greeting(name: String) -> String {
    // ownership transfers into 'greeting' below, so the parameter itself
    // is consumed rather than borrowed.
    let greeting: String = name;
    greeting
}

pub struct Counter {
    pub value: i64,
    pub history: Vec<i64>,
}

impl Counter {
    pub fn new(value: i64, history: Vec<i64>) -> Self {
        Self { value, history }
    }

    fn increment(&mut self, amount: i64) {
        self.value += amount;
        // keep a record of every value we've held
        for h in &self.history {
            println!("{h}");
        }
    }

    fn report(&self) -> i64 {
        if self.value > 100 {
            panic!("counter overflowed");
        }
        self.value
    }
}

// -- extra coverage: precedence-sensitive expressions --------------------

fn precedence_check(a: i64, b: i64, c: i64) -> i64 {
    // a - (b - c) != a - b - c -- these parens must survive rendering.
    a - (b - c)
}

fn bool_precedence_check(a: bool, b: bool, c: bool) -> bool {
    a && (b || c)
}

// -- extra coverage: an explicit `#! refer_mut` directive -----------------

fn refer_mut_example(counter: &mut i64) -> &mut i64 {
    counter
}

// -- extra coverage: collection iteration + accumulator --------------------

fn collection_example(items: &Vec<i64>) -> i64 {
    let mut total: i64 = 0;
    for x in items {
        total += x;
    }
    total
}

fn main() {
    println!("{}", clamp(150, 0, 100));
    println!("{}", sum_up_to(5));

    let name = String::from("Ferris");
    println!("{}", greet(&name));
    println!("{}", build_greeting(String::from("hello")));

    let mut counter = Counter::new(0, vec![1, 2, 3]);
    counter.increment(5);
    println!("{}", counter.report());

    println!("{}", precedence_check(10, 3, 1));
    println!("{}", bool_precedence_check(true, false, true));

    let mut n = 41;
    println!("{}", refer_mut_example(&mut n));

    println!("{}", collection_example(&vec![1, 2, 3, 4]));
}
