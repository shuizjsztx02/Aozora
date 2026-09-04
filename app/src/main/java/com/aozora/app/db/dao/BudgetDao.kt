package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.aozora.app.db.entity.BudgetEntity

@Dao
interface BudgetDao {

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insert(budget: BudgetEntity)

    @Update
    suspend fun update(budget: BudgetEntity)

    @Query("SELECT * FROM budgets WHERE id = :id")
    suspend fun getById(id: String): BudgetEntity?

    @Query("SELECT * FROM budgets WHERE bookId = :bookId ORDER BY period DESC")
    suspend fun listByBook(bookId: String): List<BudgetEntity>

    @Query("SELECT * FROM budgets WHERE bookId = :bookId AND period = :period")
    suspend fun listByBookAndPeriod(bookId: String, period: String): List<BudgetEntity>

    @Query("DELETE FROM budgets WHERE id = :id")
    suspend fun delete(id: String)
}
