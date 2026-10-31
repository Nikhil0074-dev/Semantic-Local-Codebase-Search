use std::fs;

/// Applies every .sql file in a directory in lexical order.
pub struct Migrator {
    dir: String,
}

impl Migrator {
    pub fn new(dir: &str) -> Self {
        Migrator { dir: dir.to_string() }
    }

    pub fn pending_files(&self) -> Vec<String> {
        let mut names: Vec<String> = fs::read_dir(&self.dir)
            .map(|rd| rd.filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().into_owned()).collect())
            .unwrap_or_default();
        names.sort();
        names
    }
}

fn main() {
    let m = Migrator::new("db/migrations");
    for f in m.pending_files() {
        println!("would apply {}", f);
    }
}
